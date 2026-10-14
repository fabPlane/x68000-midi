"""Close power/ground gaps without moving existing routed copper.

Run with KiCad's Python: hw/close_gaps.py logic (or breakout).
Try straight/L/45-degree tracks on the endpoints' actual layers, then a
0.45/0.25 mm through via and short stubs. Via locations are searched within
3 mm of the reported copper, including the full length of reported tracks;
a 0.05 mm grid also searches additive plane stubs up to 20 mm long.
Only GND, +3V3 and +5V may be extended. Pads, tracks, vias, copper graphics,
and all drilled holes are checked before placement; refillable zones are
checked after filling. Every trial must reduce unconnected items and must
not increase DRC errors. Fresh reports drive each iteration, and exceptions
or a worse final DRC restore the original board byte for byte.
"""

from __future__ import annotations

import heapq
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
BOARDS = {"logic": ("x68000-logic.kicad_pcb", "drc-logic.json"),
          "breakout": ("x68000-breakout.kicad_pcb", "drc-all.json")}
CLEAR = 0.15
WIDTH = 0.15
VIA_SIZE = 0.45
VIA_DRILL = 0.25
HOLE_CLEAR = 0.25
POWER_NETS = frozenset(("GND", "+3V3", "+5V"))
# Keep SWIG polygon wrappers alive until the board has been saved.
POLYS = []


def mm(v):
    return pcbnew.FromMM(v)


def to_mm(v):
    return pcbnew.ToMM(v)


def seg_distance(p, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return ((px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2) ** 0.5


def seg_seg_distance(a1, a2, b1, b2) -> float:
    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
    # Endpoint distances alone miss X-shaped crossings.
    c1, c2 = cross(a1, a2, b1), cross(a1, a2, b2)
    c3, c4 = cross(b1, b2, a1), cross(b1, b2, a2)
    if c1*c2 < 0 and c3*c4 < 0:
        return 0.0
    return min(seg_distance(a1, b1, b2), seg_distance(a2, b1, b2),
               seg_distance(b1, a1, a2), seg_distance(b2, a1, a2))


def obstacles(board, layer, net):
    """Everything on `layer` that is not on `net`, as segments and rectangles."""
    segs, rects, vias = [], [], []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetname() == net or not pad.IsOnLayer(layer):
                continue
            b = pad.GetBoundingBox()
            rects.append((to_mm(b.GetLeft()), to_mm(b.GetTop()),
                          to_mm(b.GetRight()), to_mm(b.GetBottom())))
    for t in board.Tracks():
        if t.GetNetname() == net:
            continue
        if t.Type() == pcbnew.PCB_VIA_T:
            if not t.IsOnLayer(layer):
                continue
            p = t.GetPosition()
            vias.append((to_mm(p.x), to_mm(p.y), to_mm(t.GetWidth(layer)) / 2))
        elif t.IsOnLayer(layer):
            a, b = t.GetStart(), t.GetEnd()
            segs.append(((to_mm(a.x), to_mm(a.y)), (to_mm(b.x), to_mm(b.y)),
                         to_mm(t.GetWidth()) / 2))
    return segs, rects, vias


def fits(a, b, width, segs, rects, vias) -> bool:
    r = width / 2 + CLEAR
    left, right = min(a[0], b[0]), max(a[0], b[0])
    top, bottom = min(a[1], b[1]), max(a[1], b[1])
    for s1, s2, half in segs:
        limit = r + half
        if (min(s1[0], s2[0]) > right + limit or
                max(s1[0], s2[0]) < left - limit or
                min(s1[1], s2[1]) > bottom + limit or
                max(s1[1], s2[1]) < top - limit):
            continue
        if seg_seg_distance(a, b, s1, s2) < r + half:
            return False
    for vx, vy, vr in vias:
        if seg_distance((vx, vy), a, b) < r + vr:
            return False
    for x0, y0, x1, y1 in rects:
        if x0 > right+r or x1 < left-r or y0 > bottom+r or y1 < top-r:
            continue
        if any(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in (a, b)):
            return False
        corners = [(x0,y0), (x1,y0), (x1,y1), (x0,y1)]
        if any(seg_seg_distance(a, b, p, q) < r
               for p, q in zip(corners, corners[1:]+corners[:1])):
            return False
    return True


def candidates(a, b):
    """Paths from a to b, simplest first: straight, then the two L-shaped
    routes, then the four 45-degree dog-legs. Nothing fancier -- if none of
    seven paths fits, the gap wants a human looking at it, not a search."""
    ax, ay = a
    bx, by = b
    yield [a, b]
    yield [a, (bx, ay), b]
    yield [a, (ax, by), b]
    dx, dy = bx - ax, by - ay
    step = min(abs(dx), abs(dy))
    sx = 1 if dx > 0 else -1
    sy = 1 if dy > 0 else -1
    # 45 then straight, and straight then 45, in both orientations.
    yield [a, (ax + sx * step, ay + sy * step), b]
    yield [a, (bx - sx * step, by - sy * step), b]


def score(path: Path, report: Path) -> tuple[int, int]:
    """Run a fresh DRC; never consume a previous run's report after a failure."""
    report.unlink(missing_ok=True)
    run = subprocess.run([CLI, "pcb", "drc", "--severity-all", "--format", "json",
                          "-o", str(report), str(path)], capture_output=True, text=True)
    if run.returncode or not report.exists():
        raise RuntimeError(f"DRC failed ({run.returncode}): {run.stdout} {run.stderr}")
    d = json.loads(report.read_text())
    return (sum(v.get("severity") == "error" for v in d.get("violations", [])),
            len(d.get("unconnected_items", [])))


def xy(p):
    return to_mm(p.x), to_mm(p.y)


def vector(p):
    return pcbnew.VECTOR2I(mm(p[0]), mm(p[1]))


class Copper:
    def __init__(self, board, net):
        self.board, self.net = board, net
        self.layers = list(board.GetEnabledLayers().CuStack())
        self.obs = {layer: obstacles(board, layer, net) for layer in self.layers}
        # Include copper graphics as conservative rectangles as well.
        graphics = list(board.GetDrawings())
        for fp in board.GetFootprints():
            graphics.extend(fp.GraphicalItems())
        for item in graphics:
            for layer in self.layers:
                if item.IsOnLayer(layer):
                    box = item.GetBoundingBox()
                    self.obs[layer][1].append((to_mm(box.GetLeft()), to_mm(box.GetTop()),
                                               to_mm(box.GetRight()), to_mm(box.GetBottom())))
        self.holes = []
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                drill = pad.GetDrillSize()
                if max(drill.x, drill.y):
                    # Circumscribe slots, irrespective of their angle. Check all
                    # holes, including same-net plated pads and NPTH pads.
                    self.holes.append((xy(pad.GetPosition()), to_mm(max(drill.x, drill.y))/2))
        for t in board.Tracks():
            if t.Type() == pcbnew.PCB_VIA_T:
                self.holes.append((xy(t.GetPosition()), to_mm(t.GetDrillValue())/2))
        self.zones = []
        for zone in board.Zones():
            for layer in zone.GetLayerSet().Seq():
                poly = zone.GetFilledPolysList(layer)
                POLYS.append(poly)
                self.zones.append((zone.GetNetname(), layer, poly))

    def track_fits(self, route, layer):
        return all(fits(a, b, WIDTH, *self.obs[layer])
                   for a, b in zip(route, route[1:]) if a != b)

    def via_fits(self, p):
        if any(math.dist(p, center) < VIA_DRILL/2 + radius + HOLE_CLEAR
               for center, radius in self.holes):
            return False
        return all(fits(p, p, VIA_SIZE, *self.obs[layer]) for layer in self.layers)

    def reaches_plane(self, p):
        return any(net == self.net and poly.Collide(vector(p))
                   for net, layer, poly in self.zones)


def endpoints(board, entry):
    """Use UUIDs and actual layer sets, not coordinates on an arbitrary layer."""
    lookup = {str(item.m_Uuid.AsString()): item for item in board.Tracks()}
    for fp in board.GetFootprints():
        lookup.update({str(pad.m_Uuid.AsString()): pad for pad in fp.Pads()})
    result = []
    connection = board.GetConnectivity()
    connection.Build(board)
    for reported in entry["items"]:
        item = lookup.get(reported["uuid"])
        if item is None:
            return None
        layers = [l for l in (pcbnew.F_Cu, pcbnew.B_Cu) if item.IsOnLayer(l)]
        points = [xy(item.GetPosition())]
        if item.Type() == pcbnew.PCB_TRACE_T:
            a, b = xy(item.GetStart()), xy(item.GetEnd())
            # DRC reports a track's start, even when its far end is adjacent to
            # the other item. Try the entire existing segment without moving it.
            n = max(1, math.ceil(math.dist(a, b)/0.2))
            points = [(a[0]+(b[0]-a[0])*i/n, a[1]+(b[1]-a[1])*i/n)
                      for i in range(n+1)]
        connected = list(connection.GetConnectedItems(item))
        has_via = any(i.Type() == pcbnew.PCB_VIA_T for i in connected)
        # Reach a landing on copper already connected to this endpoint, rather
        # than requiring every new stub to start at the DRC-reported pad centre.
        if not has_via:
            center = xy(item.GetPosition())
            for other in connected:
                if other.Type() != pcbnew.PCB_TRACE_T:
                    continue
                if not any(other.IsOnLayer(layer) for layer in layers):
                    continue
                a, b = xy(other.GetStart()), xy(other.GetEnd())
                n = max(1, math.ceil(math.dist(a,b)/0.2))
                for i in range(n+1):
                    p = (a[0]+(b[0]-a[0])*i/n, a[1]+(b[1]-a[1])*i/n)
                    if math.dist(center,p) <= 5.0:
                        points.append(p)
        result.append((item.GetNetname(), layers, list(dict.fromkeys(points)), has_via))
    return result


def clean_route(route):
    out = []
    for p in route:
        if not out or math.dist(out[-1], p) > 0.000001:
            out.append(p)
    return out


def first_route(copper, a, b, layer):
    for route in candidates(a, b):
        route = clean_route(route)
        if copper.track_fits(route, layer):
            return route
    return None


def proposals(board, entry):
    ends = endpoints(board, entry)
    if not ends or len(ends) != 2:
        return
    (net, la, pa, va), (net_b, lb, pb, vb) = ends
    if net not in POWER_NETS or net_b != net:
        return
    copper = Copper(board, net)
    # Direct joins must really reach both items on this layer.
    pairs = sorted(((a, b) for a in pa for b in pb), key=lambda p: math.dist(*p))
    for a, b in pairs:
        for layer in (layer for layer in la if layer in lb):
            route = first_route(copper, a, b, layer)
            if route and len(route) > 1:
                yield net, [], [(layer, route)]

    # Search local via positions, preferring little or no added track. A single
    # stub to an existing same-net plane is often enough to close the gap.
    points = set(pa + pb)
    for a, b in pairs[:20]:
        points.add(((a[0]+b[0])/2, (a[1]+b[1])/2))
    for origin in pa + pb:
        for radius in (0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0):
            for i in range(16):
                angle = i*math.pi/8
                points.add((round(origin[0]+radius*math.cos(angle), 6),
                            round(origin[1]+radius*math.sin(angle), 6)))
    points = sorted(points, key=lambda p: min(math.dist(p, a) for a in pa+pb))
    for p in points:
        if not copper.via_fits(p):
            continue
        routes = []
        for layers, anchors in ((la, pa), (lb, pb)):
            route = None
            for a in sorted(anchors, key=lambda a: math.dist(a, p)):
                for layer in layers:
                    r = first_route(copper, a, p, layer)
                    if r is not None:
                        route = (layer, r)
                        break
                if route:
                    break
            routes.append(route)
        if all(routes):
            yield net, [p], routes
        elif copper.reaches_plane(p):
            for index, route in enumerate(routes):
                # A through item already reaches every copper layer. Giving
                # it another local via cannot help the unreachable endpoint.
                if route and len(ends[index][1]) == 1 and not ends[index][3]:
                    yield net, [p], [route]

    # A short dog-leg may still be trapped by the escape fan. Search a local
    # 0.05 mm eight-direction grid for an additive stub to a clear plane via.
    # This never rips up or changes any existing copper.
    for endpoint_net, layers, anchors, has_via in ends:
        if len(layers) != 1 or has_via:
            continue
        for p, route in plane_routes(copper, anchors, layers[0]):
            yield net, [p], [(layers[0], route)]


def plane_routes(copper, anchors, layer):
    step, radius = 0.05, 20.0
    # One grid per endpoint, anchored exactly on its copper (not rounded away
    # from a narrow pad). Track samples supply multiple starts on long items.
    origin = anchors[0]
    obs = copper.obs[layer]
    left, right = origin[0]-radius, origin[0]+radius
    top, bottom = origin[1]-radius, origin[1]+radius
    segs = [s for s in obs[0] if min(s[0][0],s[1][0]) <= right+1 and
            max(s[0][0],s[1][0]) >= left-1 and min(s[0][1],s[1][1]) <= bottom+1 and
            max(s[0][1],s[1][1]) >= top-1]
    rects = [r for r in obs[1] if r[0] <= right+1 and r[2] >= left-1 and
             r[1] <= bottom+1 and r[3] >= top-1]
    vias = [v for v in obs[2] if left-1 <= v[0] <= right+1 and top-1 <= v[1] <= bottom+1]
    local = segs, rects, vias
    def point(node):
        return round(origin[0]+node[0]*step,6), round(origin[1]+node[1]*step,6)
    queue, costs, parents, roots = [], {}, {}, {}
    for anchor in anchors:
        node = (round((anchor[0]-origin[0])/step), round((anchor[1]-origin[1])/step))
        if not fits(anchor, point(node), WIDTH, *local):
            continue
        costs[node], parents[node], roots[node] = 0.0, None, anchor
        heapq.heappush(queue, (0.0,node))
    emitted = []
    while queue:
        cost, node = heapq.heappop(queue)
        if cost != costs[node]:
            continue
        p = point(node)
        if (not any(math.dist(p, q) < 0.5 for q in emitted) and
                copper.via_fits(p) and copper.reaches_plane(p)):
            nodes, cursor = [p], node
            while parents[cursor] is not None:
                cursor = parents[cursor]
                nodes.append(point(cursor))
            nodes.append(roots[cursor])
            route = clean_route(list(reversed(nodes)))
            # Greedy visibility simplification removes the grid staircase.
            simple, index = [route[0]], 0
            while index < len(route)-1:
                next_index = len(route)-1
                while not fits(route[index],route[next_index],WIDTH,*local):
                    next_index -= 1
                simple.append(route[next_index])
                index = next_index
            emitted.append(p)
            yield p, simple
        for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
            other = (node[0]+dx,node[1]+dy)
            q = point(other)
            new_cost = cost + step*math.hypot(dx,dy)
            if (new_cost > radius or new_cost >= costs.get(other, float('inf')) or
                    not fits(p,q,WIDTH,*local)):
                continue
            costs[other], parents[other] = new_cost, node
            heapq.heappush(queue,(new_cost,other))


def add_proposal(board, net, via_points, routes):
    added = []
    for p in via_points:
        via = pcbnew.PCB_VIA(board)
        via.SetPosition(vector(p))
        via.SetViaType(pcbnew.VIATYPE_THROUGH)
        via.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        via.SetWidth(mm(VIA_SIZE))
        via.SetDrill(mm(VIA_DRILL))
        via.SetNet(board.FindNet(net))
        board.Add(via)
        added.append(via)
    for layer, route in routes:
        for a, b in zip(route, route[1:]):
            track = pcbnew.PCB_TRACK(board)
            track.SetStart(vector(a))
            track.SetEnd(vector(b))
            track.SetWidth(mm(WIDTH))
            track.SetLayer(layer)
            track.SetNet(board.FindNet(net))
            board.Add(track)
            added.append(track)
    return added


def zones_clear(board, added):
    """Zones are refillable copper: check their final fill before committing.

    Testing the old fill would forbid any power via inside a ground pour even
    though the pour creates its normal clearance around the new via.
    """
    for zone in board.Zones():
        for layer in zone.GetLayerSet().Seq():
            poly = zone.GetFilledPolysList(layer)
            POLYS.append(poly)
            for item in added:
                if zone.GetNetname() == item.GetNetname() or not item.IsOnLayer(layer):
                    continue
                if item.Type() == pcbnew.PCB_VIA_T:
                    shape = item.GetPosition()
                else:
                    shape = pcbnew.SEG(item.GetStart(), item.GetEnd())
                # KiCad fill rounds at integer units; leave a 2 IU tolerance.
                width = (item.GetWidth(layer) if item.Type() == pcbnew.PCB_VIA_T
                         else item.GetWidth())
                if poly.Collide(shape, width//2 + mm(CLEAR) - 2):
                    return False
    return True


def main(argv) -> int:
    name = argv[1] if len(argv) > 1 else "logic"
    path = ROOT / "build" / BOARDS[name][0]
    with tempfile.TemporaryDirectory(prefix=f"close-gaps-{name}-") as temp:
        temp = Path(temp)
        report, backup, checkpoint = temp/"drc.json", temp/"original.kicad_pcb", temp/"checkpoint.kicad_pcb"
        before = current = score(path, report)
        shutil.copy2(path, backup)
        joined = 0
        try:
            while current[1]:
                entries = json.loads(report.read_text())["unconnected_items"]
                progressed = False
                for entry in entries:
                    board = pcbnew.LoadBoard(str(path))
                    options = proposals(board, entry)
                    shutil.copy2(path, checkpoint)
                    for net, via_points, routes in options:
                        added = add_proposal(board, net, via_points, routes)
                        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
                        if zones_clear(board, added):
                            pcbnew.SaveBoard(str(path), board)
                            after = score(path, report)
                            if after[0] <= current[0] and after[1] < current[1]:
                                print(f"{net}: vias {via_points}, tracks {routes}; {current} -> {after}", flush=True)
                                current = after
                                joined += 1
                                progressed = True
                                break
                        shutil.copy2(checkpoint, path)
                        board = pcbnew.LoadBoard(str(path))
                    if progressed:
                        break  # Recompute the ratsnest after each accepted join.
                if not progressed:
                    break
            after = score(path, report)
            if after[0] > before[0] or after[1] > before[1]:
                raise RuntimeError(f"Final DRC worsened: {before} -> {after}")
        except BaseException:
            shutil.copy2(backup, path)
            raise
        print(f"{name}: joined {joined}; (errors, unconnected) {before} -> {after}")
        for entry in json.loads(report.read_text())["unconnected_items"]:
            print("  no clear improving candidate: " + " -> ".join(
                f"{i['description']} at {i['pos']}" for i in entry["items"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
