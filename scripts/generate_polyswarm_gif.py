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

# Player position
PX, PY = 720, 135

# Combat tuning
FIRE_EVERY = 5        # lower = faster shooting
DMG_PER_HIT = 1       # damage per tick
MAX_ENEMIES = 28      # enemies spawned from contribution blocks

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

        # GitHub can return partial weeks (<7). Pad to 7.
        if len(counts) < 7:
            counts = counts + [0] * (7 - len(counts))
        elif len(counts) > 7:
            counts = counts[:7]

        cols.append(counts)

    # Normalize to exactly GRID_COLS weeks
    if len(cols) > GRID_COLS:
        cols = cols[-GRID_COLS:]
    elif len(cols) < GRID_COLS:
        pad_cols = [[0] * 7 for _ in range(GRID_COLS - len(cols))]
        cols = pad_cols + cols

    # Convert to row-major [row][col]
    grid = [[0] * GRID_COLS for _ in range(GRID_ROWS)]
    for c in range(GRID_COLS):
        for r in range(GRID_ROWS):
            grid[r][c] = cols[c][r]

    return grid


# -----------------------------
# Drawing helpers
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
    # subtle grid lines
    for i in range(GRID_ROWS):
        y = GRID_Y0 + i * (CELL + GAP) + CELL / 2
        draw.line((GRID_X0, y, GRID_X0 + GRID_COLS * (CELL + GAP), y), fill=GRID_LINE, width=1)
    for j in range(0, GRID_COLS, 7):
        x = GRID_X0 + j * (CELL + GAP) + CELL / 2
        draw.line((x, GRID_Y0, x, GRID_Y0 + GRID_ROWS * (CELL + GAP)), fill=GRID_LINE, width=1)


def draw_hud(draw: ImageDraw.ImageDraw, subtitle: str):
    draw.text((26, 18), "POLYGON SWARM // contributions -> enemies", fill=TEXT)
    draw.text((26, 36), subtitle, fill=TEXT_DIM)


# -----------------------------
# Combat simulation helpers
# -----------------------------
def enemy_pos(e, t_swarm):
    # Move enemies toward player with slight wobble (vampire-survivors vibe)
    dx = PX - e["x0"]
    dy = PY - e["y0"]
    dist = max(1.0, math.hypot(dx, dy))
    ux, uy = dx / dist, dy / dist

    ex = e["x0"] + ux * (t_swarm * e["speed"] * 1.6) + math.sin(t_swarm * 0.12 + e["phase"]) * 10
    ey = e["y0"] + uy * (t_swarm * e["speed"] * 1.6) + math.cos(t_swarm * 0.11 + e["phase"]) * 8

    ex = max(30, min(W - 30, ex))
    ey = max(30, min(H - 30, ey))
    return ex, ey


def pick_target(enemies, t_swarm):
    best = None
    best_d = 10**18
    best_xy = None
    for e in enemies:
        if not e.get("alive"):
            continue
        ex, ey = enemy_pos(e, t_swarm)
        d = (ex - PX) ** 2 + (ey - PY) ** 2
        if d < best_d:
            best_d = d
            best = e
            best_xy = (ex, ey)
    return best, best_xy


def apply_damage(e, dmg=1):
    e["hp"] -= dmg
    e["hit_flash"] = 3
    if e["hp"] <= 0:
        e["alive"] = False
        e["death_fx"] = 6  # small fade frames


# -----------------------------
# Frame generation
# -----------------------------
def make_frames(grid):
    os.makedirs("assets", exist_ok=True)

    seed = int(dt.date.today().strftime("%Y%m%d"))
    rnd = random.Random(seed)

    coords = [(r, c) for r in range(GRID_ROWS) for c in range(GRID_COLS)]

    # Reveal order: weighted by contribution level
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
    swarm_frames = 90

    # Choose enemy source blocks (top activity)
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
                "hp": 2 + lv * 2,     # tougher for higher activity blocks
                "alive": True,
                "hit_flash": 0,
                "death_fx": 0,
            }
        )

    frames = []

    def render(revealed_set, t_swarm=None):
        im = Image.new("RGBA", (W, H), BG)
        draw = ImageDraw.Draw(im)
        draw_background(draw)

        # Contribution cells
        for r in range(GRID_ROWS):
            for c in range(GRID_COLS):
                x, y = cell_pos(r, c)
                if (r, c) not in revealed_set:
                    col = (10, 14, 13)
                else:
                    col = LEVEL_COLORS[level(grid[r][c])]
                draw.rounded_rectangle((x, y, x + CELL, y + CELL), radius=3, fill=col)

        # Player
        draw.ellipse((PX - 22, PY - 22, PX + 22, PY + 22), outline=(0, 255, 127, 255), width=2)
        poly = [(PX, PY - 14), (PX + 12, PY - 4), (PX + 8, PY + 12), (PX - 8, PY + 12), (PX - 12, PY - 4)]
        draw.polygon(poly, outline=(0, 255, 127, 255), fill=(0, 255, 127, 30))

        if t_swarm is None:
            return im

        # Dim the source blocks that "became" enemies
        for e in enemies:
            r, c = e["src"]
            x, y = cell_pos(r, c)
            draw.rounded_rectangle((x, y, x + CELL, y + CELL), radius=3, fill=(8, 16, 13))

        # FIRE: aim laser at nearest enemy and apply damage
        lasers = []
        if t_swarm % FIRE_EVERY == 0:
            target, txy = pick_target(enemies, t_swarm)
            if target and txy:
                apply_damage(target, DMG_PER_HIT)
                lasers.append((PX, PY, txy[0], txy[1]))

        # update hit flash + death fx timers
        for e in enemies:
            if e["hit_flash"] > 0:
                e["hit_flash"] -= 1
            if (not e["alive"]) and e["death_fx"] > 0:
                e["death_fx"] -= 1

        # Draw lasers (glow + core line)
        for (x1, y1, x2, y2) in lasers:
            draw.line((x1, y1, x2, y2), fill=(0, 255, 127, 120), width=6)
            draw.line((x1, y1, x2, y2), fill=(0, 255, 127, 235), width=2)

        # Draw enemies (alive only); dead have a brief fade-out sparkle
        for e in enemies:
            ex, ey = enemy_pos(e, t_swarm)
            r0 = e["rad"]

            if e["alive"]:
                if e["hit_flash"] > 0:
                    fill = (0, 255, 127, ENEMY_HIT_ALPHA)
                else:
                    fill = (0, 255, 127, ENEMY_ALPHA)
                draw.ellipse((ex - r0, ey - r0, ex + r0, ey + r0), fill=fill)
            else:
                if e["death_fx"] > 0:
                    a = int(180 * (e["death_fx"] / 6))
                    # tiny "xp pop"
                    draw.ellipse((ex - 2, ey - 2, ex + 2, ey + 2), fill=(0, 255, 127, a))

        return im

    # Phase A: build (reveal squares gradually)
    revealed = set()
    for i in range(build_frames):
        target = int((i + 1) / build_frames * total_cells)
        while len(revealed) < min(target, len(appear_order)):
            revealed.add(appear_order[len(revealed)])

        frame = render(revealed, None)
        d = ImageDraw.Draw(frame)
        draw_hud(d, "phase: BUILD // forging blocks from commits")
        frames.append(frame.convert("P", palette=Image.ADAPTIVE))

    # Phase B: swarm (blocks become enemies + targeted laser kills)
    for t in range(swarm_frames):
        frame = render(revealed, t_swarm=t)
        d = ImageDraw.Draw(frame)
        draw_hud(d, "phase: SWARM // laser targets • enemies disappear")
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