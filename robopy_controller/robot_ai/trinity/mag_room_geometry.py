"""
Robust 2D Spatial Geometry Engine for Marcus Semantic Room Registry.
Pure Python standard library implementation with zero mandatory external GIS dependencies.
Optional transparent acceleration via Shapely if installed.
"""

from typing import List, Tuple, Sequence, Optional
import math

try:
    from shapely.geometry import Point as ShapelyPoint, Polygon as ShapelyPolygon
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False


def compute_bounding_box(polygon: Sequence[Sequence[float]]) -> Tuple[float, float, float, float]:
    """
    Computes axis-aligned bounding box [xmin, ymin, xmax, ymax].
    
    Args:
        polygon: Sequence of (x, y) vertex coordinates.
        
    Returns:
        (xmin, ymin, xmax, ymax)
    """
    if not polygon or len(polygon) < 3:
        raise ValueError(f"Polygon must have at least 3 vertices, got {len(polygon) if polygon else 0}")
        
    xs = [float(p[0]) for p in polygon]
    ys = [float(p[1]) for p in polygon]
    return (min(xs), min(ys), max(xs), max(ys))


def compute_polygon_centroid(polygon: Sequence[Sequence[float]]) -> Tuple[float, float]:
    """
    Computes the geometric center of mass (centroid) for an arbitrary
    non-self-intersecting 2D polygon using the Shoelace formula (Green's theorem).
    
    Invariant to vertex winding order (clockwise or counter-clockwise).
    Handles degenerate collinear cases gracefully by fallback to arithmetic mean.
    
    Formula:
        A = 1/2 * sum(x_i * y_{i+1} - x_{i+1} * y_i)
        C_x = 1/(6*A) * sum((x_i + x_{i+1}) * (x_i * y_{i+1} - x_{i+1} * y_i))
        C_y = 1/(6*A) * sum((y_i + y_{i+1}) * (x_i * y_{i+1} - x_{i+1} * y_i))
    """
    n = len(polygon)
    if n == 0:
        return (0.0, 0.0)
    if n < 3:
        return (sum(float(p[0]) for p in polygon) / n, sum(float(p[1]) for p in polygon) / n)

    # Sanitize: if polygon is closed with duplicate end point, strip it
    poly = [[float(p[0]), float(p[1])] for p in polygon]
    if math.isclose(poly[0][0], poly[-1][0], abs_tol=1e-9) and math.isclose(poly[0][1], poly[-1][1], abs_tol=1e-9):
        poly = poly[:-1]
        n = len(poly)
        if n < 3:
            return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)

    signed_area = 0.0
    cx = 0.0
    cy = 0.0

    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        cross = (x0 * y1 - x1 * y0)
        signed_area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross

    signed_area *= 0.5

    # Degenerate case: collinear or zero-area polygon
    if abs(signed_area) < 1e-9:
        return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)

    factor = 1.0 / (6.0 * signed_area)
    return (float(cx * factor), float(cy * factor))


def point_on_segment(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
    tol: float = 1e-7
) -> bool:
    """
    Checks if point (px, py) lies on line segment (ax, ay)-(bx, by)
    within distance tolerance 'tol'. Fast rejection via bounding box and cross product.
    """
    # 1. Segment Bounding Box Rejection
    if px < min(ax, bx) - tol or px > max(ax, bx) + tol or \
       py < min(ay, by) - tol or py > max(ay, by) + tol:
        return False

    dx = bx - ax
    dy = by - ay
    seg_len_sq = dx * dx + dy * dy

    # Degenerate segment (single point)
    if seg_len_sq < tol * tol:
        return (px - ax) ** 2 + (py - ay) ** 2 <= tol * tol

    # 2. Perpendicular distance test via cross-product
    cross = (px - ax) * dy - (py - ay) * dx
    if (cross * cross) > (tol * tol * seg_len_sq):
        return False

    # 3. Projection test along segment via dot-product
    dot = (px - ax) * dx + (py - ay) * dy
    return -tol <= dot <= seg_len_sq + tol


def point_in_polygon(
    point: Tuple[float, float],
    polygon: Sequence[Sequence[float]],
    bounding_box: Optional[Tuple[float, float, float, float]] = None,
    include_boundary: bool = True,
    tol: float = 1e-7
) -> bool:
    """
    Robust Point-in-Polygon (PIP) test.
    
    1. O(1) Bounding box pre-filter.
    2. Exact boundary and vertex check.
    3. Jordan curve ray-casting with Franklin PNPoly half-open interval rule.
    4. Optional Shapely acceleration if installed.
    """
    px, py = float(point[0]), float(point[1])
    n = len(polygon)
    if n < 3:
        return False

    # Clean closed ring duplicate if present
    poly = [[float(p[0]), float(p[1])] for p in polygon]
    if math.isclose(poly[0][0], poly[-1][0], abs_tol=1e-9) and math.isclose(poly[0][1], poly[-1][1], abs_tol=1e-9):
        poly = poly[:-1]
        n = len(poly)
        if n < 3:
            return False

    # 1. Fast Bounding Box Filter
    if bounding_box is not None:
        min_x, min_y, max_x, max_y = bounding_box
    else:
        min_x = min(p[0] for p in poly)
        max_x = max(p[0] for p in poly)
        min_y = min(p[1] for p in poly)
        max_y = max(p[1] for p in poly)

    if px < min_x - tol or px > max_x + tol or py < min_y - tol or py > max_y + tol:
        return False

    # 2. Boundary and Vertex Check
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        if point_on_segment(px, py, ax, ay, bx, by, tol=tol):
            return include_boundary

    # 3. Optional Shapely Acceleration (if available)
    if HAS_SHAPELY:
        try:
            sp_poly = ShapelyPolygon(poly)
            sp_pt = ShapelyPoint(px, py)
            return sp_poly.contains(sp_pt) or (include_boundary and sp_poly.touches(sp_pt))
        except Exception:
            pass  # Fall through to pure Python engine

    # 4. Franklin PNPoly Ray Casting Engine
    inside = False
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]

        # Half-open vertical interval ((y1 > py) != (y2 > py))
        # Mathematically avoids ray-vertex double-crossing
        if (y1 > py) != (y2 > py):
            x_int = x1 + (x2 - x1) * (py - y1) / (y2 - y1)
            if px < x_int:
                inside = not inside

    return inside
