import os
import math
import random
import datetime as dt
import requests
from PIL import Image, ImageDraw, ImageFont

# ---------- Config ----------
USERNAME = os.environ.get("GH_USERNAME", "").strip()
TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
OUT_PATH = "assets/polygon-swarm.gif"

W, H = 900, 240
PAD = 20

GRID_ROWS = 7
GRID_COLS = 53  # GitHub calendar weeks ~ 52-53

CELL = 11
GAP = 4

GRID_X0 = 30
GRID_Y0 = 50

BG = (7, 16, 12)
GRID_LINE = (0, 255, 127, 18)  # subtle
TEXT = (0, 255, 127, 255)

# Contribution colors by intensity
LEVEL_COLORS = [
    (18, 35, 28),    # 0
    (15, 70, 45),    # 1
    (0, 140, 75),    # 2
    (0, 200, 95),    # 3
    (0, 255, 127),   # 4
]

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
    # Past year contribution calendar
    query = """
    query($login:String!) {
      user(login:$login) {
        contributionsCollection {
          contributionCalendar {
            weeks {
              contributionDays {
                contributionCount
                weekday
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

    # weeks length can be 52-53; normalize to GRID_COLS by trimming/padding left
    # Each week has 7 days, weekday is 0..6 (Sun..Sat), but contributionDays already ordered Sun..Sat.
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

    # Ensure exactly GRID_COLS
    if len(cols) > GRID_COLS:
        cols = cols[-GRID_COLS:]
    elif len(cols) < GRID_COLS:
        pad_cols = [[0]*7 for _ in range(GRID_COLS - len(cols))]
        cols = pad_cols + cols

    # Convert to row-major grid [row][col] where row 0 is top (Mon-ish visually),
    # GitHub shows Sun top sometimes; we'll keep as given (Sun..Sat) but looks fine for effect.
    grid = [[0]*GRID_COLS for _ in range(GRID_ROWS)]
    for c in range(GRID_COLS):
        for r in range(GRID_ROWS):
            grid[r][c] = cols[c][r]

    return grid

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
    for i in range(6):
        y = GRID_Y0 + i * (CELL + GAP) + CELL/2
        draw.line((GRID_X0, y, GRID_X0 + GRID_COLS*(CELL+GAP), y), fill=GRID_LINE, width=1)
    for j in range(0, GRID_COLS, 7):
        x = GRID_X0 + j * (CELL + GAP) + CELL/2
        draw.line((x, GRID_Y0, x, GRID_Y0 + GRID_ROWS*(CELL+GAP)), fill=GRID_LINE, width=1)

def draw_hud(draw: ImageDraw.ImageDraw, subtitle: str):
    draw.text((26, 18), "POLYGON SWARM // contributions -> enemies", fill=TEXT)
    draw.text((26, 36), subtitle, fill=(0, 255, 127, 180))

def make_frames(grid):
    os.makedirs("assets", exist_ok=True)

    # Deterministic randomness
    seed = int(dt.date.today().strftime("%Y%m%d"))
    rnd = random.Random(seed)

    # Flatten nonzero cells for "spawn" ordering
    coords = [(r, c) for r in range(GRID_ROWS) for c in range(GRID_COLS)]
    # weighted by activity to appear earlier
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

    # Phase A: build-in animation (frames)
    build_frames = 55
    # Phase B: swarm animation (frames)
    swarm_frames = 70

    # choose which cells become enemies (top N active)
    active_cells = sorted(
        [(grid[r][c], r, c) for r in range(GRID_ROWS) for c in range(GRID_COLS)],
        reverse=True
    )
    # pick up to 26 enemies with count>0
    enemies_src = [(r, c) for (cnt, r, c) in active_cells if cnt > 0][:26]
    if not enemies_src:
        enemies_src = [(3, 40), (2, 41), (4, 42)]  # fallback

    # player position (center-ish right)
    px, py = 720, 135

    # initial enemy positions (from cell centers)
    enemies = []
    for (r, c) in enemies_src:
        x, y = cell_pos(r, c)
        enemies.append({
            "x0": x + CELL/2,
            "y0": y + CELL/2,
            "phase": rnd.random() * math.tau,
            "speed": 0.8 + rnd.random() * 1.2,
            "rad": 3 + level(grid[r][c]),
        })

    frames = []

    def render(base_revealed_set, t_swarm=None):
        im = Image.new("RGBA", (W, H), BG)
        draw = ImageDraw.Draw(im)

        draw_background(draw)

        # Draw contribution cells
        for r in range(GRID_ROWS):
            for c in range(GRID_COLS):
                if (r, c) not in base_revealed_set:
                    # unrevealed -> dark placeholder
                    col = (12, 18, 16)
                else:
                    lv = level(grid[r][c])
                    col = LEVEL_COLORS[lv]
                x, y = cell_pos(r, c)
                draw.rounded_rectangle((x, y, x+CELL, y+CELL), radius=3, fill=col)

        # Player core + pulse ring
        draw.ellipse((px-22, py-22, px+22, py+22), outline=(0,255,127,255), width=2)
        # small polygon
        poly = [(px, py-14), (px+12, py-4), (px+8, py+12), (px-8, py+12), (px-12, py-4)]
        draw.polygon(poly, outline=(0,255,127,255), fill=(0,255,127,30))

        # Swarm phase overlays
        if t_swarm is not None:
            # make some enemies detach from their cells: we "erase" their source square to look like it became the enemy
            for (src, e) in zip(enemies_src, enemies):
                r, c = src
                x, y = cell_pos(r, c)
                # dim source block
                draw.rounded_rectangle((x, y, x+CELL, y+CELL), radius=3, fill=(10, 20, 16))

            # enemies move toward player with a little orbit noise
            for e in enemies:
                dx = px - e["x0"]
                dy = py - e["y0"]
                dist = max(1.0, math.hypot(dx, dy))
                ux, uy = dx/dist, dy/dist

                # approach factor
                k = 0.55 + 0.45*math.sin(t_swarm*0.08 + e["phase"])
                ex = e["x0"] + ux * (t_swarm * e["speed"] * 1.6) + math.sin(t_swarm*0.12 + e["phase"]) * 10
                ey = e["y0"] + uy * (t_swarm * e["speed"] * 1.6) + math.cos(t_swarm*0.11 + e["phase"]) * 8

                # clamp a bit
                ex = max(30, min(W-30, ex))
                ey = max(30, min(H-30, ey))

                # enemy draw
                r0 = e["rad"]
                draw.ellipse((ex-r0, ey-r0, ex+r0, ey+r0), fill=(0,255,127,110))

            # “tick” projectiles (vampire survivors vibe)
            if int(t_swarm) % 12 == 0:
                draw.line((px, py, px-150, py+60), fill=(0,255,127,200), width=2)
                draw.line((px, py, px-120, py-55), fill=(0,255,127,200), width=2)

        return im.convert("P", palette=Image.ADAPTIVE)

    # Build phase: reveal squares gradually
    revealed = set()
    for i in range(build_frames):
        # reveal a chunk each frame
        target = int((i+1) / build_frames * total_cells)
        while len(revealed) < min(target, len(appear_order)):
            revealed.add(appear_order[len(revealed)])
        subtitle = "phase: BUILD // forging blocks from commits"
        im = Image.new("RGBA", (W, H), BG)
        draw = ImageDraw.Draw(im)
        draw_hud(draw, subtitle)
        frame = render(revealed, None)
        # re-draw HUD on top after palette conversion? easiest: render HUD before palette conversion in render, so do here:
        # We'll just paste text onto palettized frame using RGBA conversion:
        fr = frame.convert("RGBA")
        d2 = ImageDraw.Draw(fr)
        draw_hud(d2, subtitle)
        frames.append(fr.convert("P", palette=Image.ADAPTIVE))

    # Swarm phase: enemies detach and move
    for t in range(swarm_frames):
        subtitle = "phase: SWARM // blocks become entities"
        fr = render(revealed, t_swarm=t)
        fr = fr.convert("RGBA")
        d2 = ImageDraw.Draw(fr)
        draw_hud(d2, subtitle)
        frames.append(fr.convert("P", palette=Image.ADAPTIVE))

    return frames

def main():
    grid = fetch_contrib_grid()
    frames = make_frames(grid)

    # Save GIF
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