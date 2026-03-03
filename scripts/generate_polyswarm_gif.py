import os
import math
import random
import datetime as dt
import requests
from PIL import Image, ImageDraw

# -----------------------------
# Config
# -----------------------------
USERNAME = os.environ.get("GH_USERNAME", "").strip()
TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()

OUT_PATH = "assets/polygon-swarm.gif"

W, H = 900, 240

GRID_ROWS = 7
GRID_COLS = 53

CELL = 11
GAP = 4

GRID_X0 = 30
GRID_Y0 = 60

BG = (7, 16, 12)
TEXT = (0, 255, 127, 255)
TEXT_DIM = (0, 255, 127, 180)

GRID_LINE = (0, 255, 127, 18)

LEVEL_COLORS = [
    (12, 18, 16),   # 0
    (15, 70, 45),   # 1
    (0, 140, 75),   # 2
    (0, 200, 95),   # 3
    (0, 255, 127),  # 4
]

# Base player position (right side)
PX0, PY0 = 720, 135

# Combat tuning
FIRE_EVERY = 6         # lower = faster firing
DMG_PER_HIT = 1        # damage per chain hit
CHAIN_TARGETS = 3
CHAIN_RANGE = 170
MAX_ENEMIES = 28

# Player movement (kiting)
PLAYER_MOVE_X = 42
PLAYER_MOVE_Y = 18
PLAYER_JITTER = 5
PLAYER_SPEED = 0.085

# Dodge behavior
DODGE_TRIGGER = 52      # if enemy is within this radius -> dodge
DODGE_COOLDOWN = 18     # frames between dodges
DODGE_DISTANCE = 60     # dash distance
DODGE_SMOOTH = 0.55     # smoothing factor for dash direction

# Visual tuning
ENEMY_ALPHA = 120
ENEMY_HIT_ALPHA = 230


# -----------------------------
# GitHub GraphQL helpers
# -----------------------------
def gql(query: str, variables: dict):
    if not TOKEN or not USERNAME:
        raise SystemExit("Missing GITHUB_TOKEN or GH_USERNAME env vars.")
    r = requests.post(
        "https://api.github.com/graphql",
        json={"query": query, "variables": variables},
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]


def fetch_contrib_grid():
    query = """
    query($login:String!) {
      user(login:$login) {
        contributionsCollection {
          contributionCalendar {
            weeks {
              contributionDays {
                contributionCount
                date
              }
            }
          }
        }
      }
    }
    """
    data = gql(query, {"login": USERNAME})
    weeks = data["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]

    cols = []
    for w in weeks:
        days = w.get("contributionDays", [])
        counts = [d.get("contributionCount", 0) for d in days]

        # partial weeks possible -> pad
        if len(counts) < 7:
            counts = counts + [0] * (7 - len(counts))
        elif len(counts) > 7:
            counts = counts[:7]

        cols.append(counts)

    # normalize to GRID_COLS
    if len(cols) > GRID_COLS:
        cols = cols[-GRID_COLS:]
    elif len(cols) < GRID_COLS:
        pad_cols = [[0] * 7 for _ in range(GRID_COLS - len(cols))]
        cols = pad_cols + cols

    grid = [[0] * GRID_COLS for _ in range(GRID_ROWS)]
    for c in range(GRID_COLS):
        for r in range(GRID_ROWS):
            grid[r][c] = cols[c][r]
    return grid


# -----------------------------
# Helpers
# -----------------------------
def level(count: int) -> int:
    if count <= 0:
        return 0
    if count == 1:
        return 1
    if count <= 3:
        return 2
    if count <= 6:
        return 3
    return 4


def cell_pos(r, c):
    x = GRID_X0 + c * (CELL + GAP)
    y = GRID_Y0 + r * (CELL + GAP)
    return x, y


def draw_background(draw: ImageDraw.ImageDraw):
    for i in range(GRID_ROWS):
        y = GRID_Y0 + i * (CELL + GAP) + CELL / 2
        draw.line((GRID_X0, y, GRID_X0 + GRID_COLS * (CELL + GAP), y), fill=GRID_LINE, width=1)
    for j in range(0, GRID_COLS, 7):
        x = GRID_X0 + j * (CELL + GAP) + CELL / 2
        draw.line((x, GRID_Y0, x, GRID_Y0 + GRID_ROWS * (CELL + GAP)), fill=GRID_LINE, width=1)


def draw_hud(draw: ImageDraw.ImageDraw, subtitle: str):
    draw.text((26, 18), "POLYGON SWARM // contributions -> enemies", fill=TEXT)
    draw.text((26, 36), subtitle, fill=TEXT_DIM)


def dist2(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def player_base_pos(t_swarm: int):
    t = t_swarm * PLAYER_SPEED
    x = PX0 + math.sin(t) * PLAYER_MOVE_X + math.sin(t * 2.4) * PLAYER_JITTER
    y = PY0 + math.cos(t * 0.9) * PLAYER_MOVE_Y + math.cos(t * 2.0) * PLAYER_JITTER
    x = max(520, min(W - 40, x))
    y = max(60, min(H - 40, y))
    return x, y


def enemy_pos(e, px, py, t_swarm):
    dx = px - e["x0"]
    dy = py - e["y0"]
    dist = max(1.0, math.hypot(dx, dy))
    ux, uy = dx / dist, dy / dist

    ex = e["x0"] + ux * (t_swarm * e["speed"] * 1.6) + math.sin(t_swarm * 0.12 + e["phase"]) * 10
    ey = e["y0"] + uy * (t_swarm * e["speed"] * 1.6) + math.cos(t_swarm * 0.11 + e["phase"]) * 8

    ex = max(30, min(W - 30, ex))
    ey = max(30, min(H - 30, ey))
    return ex, ey


def apply_damage(e, dmg=1):
    e["hp"] -= dmg
    e["hit_flash"] = 3
    if e["hp"] <= 0:
        e["alive"] = False
        e["death_fx"] = 7


def build_enemy_positions(enemies, px, py, t_swarm):
    pos = {}
    for idx, e in enumerate(enemies):
        if not e.get("alive"):
            continue
        pos[idx] = enemy_pos(e, px, py, t_swarm)
    return pos


def pick_chain_targets(positions, start_xy, k=3, max_jump=170):
    chosen = []
    remaining = set(positions.keys())
    cur_xy = start_xy
    max_jump2 = max_jump * max_jump

    for _ in range(k):
        best = None
        best_d = 10**18
        for idx in remaining:
            d = dist2(cur_xy, positions[idx])
            if d < best_d:
                best_d = d
                best = idx
        if best is None:
            break
        if chosen and best_d > max_jump2:
            break
        chosen.append(best)
        remaining.remove(best)
        cur_xy = positions[best]
    return chosen


def dodge_adjust(px, py, positions, dodge_state):
    """
    If any enemy is too close, dash away.
    dodge_state: dict with keys cooldown, vx, vy
    """
    if dodge_state["cooldown"] > 0:
        dodge_state["cooldown"] -= 1
        return px, py

    trigger2 = DODGE_TRIGGER * DODGE_TRIGGER
    nearest = None
    nearest_d = 10**18

    for xy in positions.values():
        d = dist2((px, py), xy)
        if d < nearest_d:
            nearest_d = d
            nearest = xy

    if nearest is None or nearest_d > trigger2:
        return px, py

    # dash direction: away from nearest enemy
    dx = px - nearest[0]
    dy = py - nearest[1]
    mag = max(1.0, math.hypot(dx, dy))
    ux, uy = dx / mag, dy / mag

    # smooth direction changes (less jittery)
    dodge_state["vx"] = dodge_state["vx"] * (1 - DODGE_SMOOTH) + ux * DODGE_SMOOTH
    dodge_state["vy"] = dodge_state["vy"] * (1 - DODGE_SMOOTH) + uy * DODGE_SMOOTH

    px2 = px + dodge_state["vx"] * DODGE_DISTANCE
    py2 = py + dodge_state["vy"] * DODGE_DISTANCE

    px2 = max(520, min(W - 40, px2))
    py2 = max(60, min(H - 40, py2))

    dodge_state["cooldown"] = DODGE_COOLDOWN
    return px2, py2


# -----------------------------
# Frame generation
# -----------------------------
def make_frames(grid):
    os.makedirs("assets", exist_ok=True)

    seed = int(dt.date.today().strftime("%Y%m%d"))
    rnd = random.Random(seed)

    coords = [(r, c) for r in range(GRID_ROWS) for c in range(GRID_COLS)]

    weighted = []
    for (r, c) in coords:
        w = 1 + level(grid[r][c]) * 2
        weighted += [(r, c)] * w
    rnd.shuffle(weighted)

    appear_order = []
    seen = set()
    for rc in weighted:
        if rc not in seen:
            seen.add(rc)
            appear_order.append(rc)

    total_cells = GRID_ROWS * GRID_COLS
    build_frames = 55
    swarm_frames = 110

    active_cells = sorted(
        [(grid[r][c], r, c) for r in range(GRID_ROWS) for c in range(GRID_COLS)],
        reverse=True,
    )
    enemies_src = [(r, c) for (cnt, r, c) in active_cells if cnt > 0][:MAX_ENEMIES]
    if not enemies_src:
        enemies_src = [(3, 40), (2, 41), (4, 42)]

    enemies = []
    for (r, c) in enemies_src:
        x, y = cell_pos(r, c)
        lv = level(grid[r][c])
        enemies.append(
            {
                "src": (r, c),
                "x0": x + CELL / 2,
                "y0": y + CELL / 2,
                "phase": rnd.random() * math.tau,
                "speed": 0.7 + rnd.random() * 1.6,
                "rad": 3 + lv,
                "hp": 2 + lv * 2,
                "alive": True,
                "hit_flash": 0,
                "death_fx": 0,
            }
        )

    frames = []
    dodge_state = {"cooldown": 0, "vx": 0.0, "vy": 0.0}

    def render(revealed_set, t_swarm=None):
        im = Image.new("RGBA", (W, H), BG)
        draw = ImageDraw.Draw(im)
        draw_background(draw)

        # contribution grid
        for r in range(GRID_ROWS):
            for c in range(GRID_COLS):
                x, y = cell_pos(r, c)
                if (r, c) not in revealed_set:
                    col = (10, 14, 13)
                else:
                    col = LEVEL_COLORS[level(grid[r][c])]
                draw.rounded_rectangle((x, y, x + CELL, y + CELL), radius=3, fill=col)

        if t_swarm is None:
            px, py = PX0, PY0
        else:
            px, py = player_base_pos(t_swarm)

        # player draw
        draw.ellipse((px - 22, py - 22, px + 22, py + 22), outline=(0, 255, 127, 255), width=2)
        poly = [(px, py - 14), (px + 12, py - 4), (px + 8, py + 12), (px - 8, py + 12), (px - 12, py - 4)]
        draw.polygon(poly, outline=(0, 255, 127, 255), fill=(0, 255, 127, 30))

        if t_swarm is None:
            return im

        # dim enemy source blocks
        for e in enemies:
            r, c = e["src"]
            x, y = cell_pos(r, c)
            draw.rounded_rectangle((x, y, x + CELL, y + CELL), radius=3, fill=(8, 16, 13))

        # enemy positions relative to current player
        positions = build_enemy_positions(enemies, px, py, t_swarm)

        # dodge adjustment after seeing threats
        px, py = dodge_adjust(px, py, positions, dodge_state)

        # redraw player after dodge (so it shows the dash result)
        draw.ellipse((px - 22, py - 22, px + 22, py + 22), outline=(0, 255, 127, 255), width=2)
        poly = [(px, py - 14), (px + 12, py - 4), (px + 8, py + 12), (px - 8, py + 12), (px - 12, py - 4)]
        draw.polygon(poly, outline=(0, 255, 127, 255), fill=(0, 255, 127, 30))

        # recompute positions after dodge for better aiming
        positions = build_enemy_positions(enemies, px, py, t_swarm)

        # chain lightning
        chain_idxs = []
        if (t_swarm % FIRE_EVERY == 0) and positions:
            chain_idxs = pick_chain_targets(positions, (px, py), CHAIN_TARGETS, CHAIN_RANGE)
            for idx in chain_idxs:
                apply_damage(enemies[idx], DMG_PER_HIT)

        # update timers
        for e in enemies:
            if e["hit_flash"] > 0:
                e["hit_flash"] -= 1
            if (not e["alive"]) and e["death_fx"] > 0:
                e["death_fx"] -= 1

        # draw chain lightning
        if chain_idxs:
            pts = [(px, py)] + [positions[idx] for idx in chain_idxs]
            for i in range(len(pts) - 1):
                x1, y1 = pts[i]
                x2, y2 = pts[i + 1]
                draw.line((x1, y1, x2, y2), fill=(0, 255, 127, 110), width=7)
            for i in range(len(pts) - 1):
                x1, y1 = pts[i]
                x2, y2 = pts[i + 1]
                draw.line((x1, y1, x2, y2), fill=(0, 255, 127, 235), width=2)
            for idx in chain_idxs:
                tx, ty = positions[idx]
                draw.ellipse((tx - 2, ty - 2, tx + 2, ty + 2), fill=(0, 255, 127, 220))

        # draw enemies
        for idx, e in enumerate(enemies):
            ex, ey = positions.get(idx, enemy_pos(e, px, py, t_swarm))
            r0 = e["rad"]
            if e["alive"]:
                fill = (0, 255, 127, ENEMY_HIT_ALPHA if e["hit_flash"] > 0 else ENEMY_ALPHA)
                draw.ellipse((ex - r0, ey - r0, ex + r0, ey + r0), fill=fill)
            else:
                if e["death_fx"] > 0:
                    a = int(200 * (e["death_fx"] / 7))
                    draw.ellipse((ex - 2, ey - 2, ex + 2, ey + 2), fill=(0, 255, 127, a))

        return im

    # build phase
    revealed = set()
    for i in range(build_frames):
        target = int((i + 1) / build_frames * total_cells)
        while len(revealed) < min(target, len(appear_order)):
            revealed.add(appear_order[len(revealed)])
        frame = render(revealed, None)
        d = ImageDraw.Draw(frame)
        draw_hud(d, "phase: BUILD // forging blocks from commits")
        frames.append(frame.convert("P", palette=Image.ADAPTIVE))

    # swarm phase
    for t in range(swarm_frames):
        frame = render(revealed, t_swarm=t)
        d = ImageDraw.Draw(frame)
        draw_hud(d, "phase: SWARM // player kites + dodges • chain lightning")
        frames.append(frame.convert("P", palette=Image.ADAPTIVE))

    return frames


def main():
    grid = fetch_contrib_grid()
    frames = make_frames(grid)

    frames[0].save(
        OUT_PATH,
        save_all=True,
        append_images=frames[1:],
        duration=70,
        loop=0,
        disposal=2,
        optimize=False,
    )
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()