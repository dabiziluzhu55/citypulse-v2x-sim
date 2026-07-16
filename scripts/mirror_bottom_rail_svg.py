"""Mirror bottom-rail-left.svg into bottom-rail-right.svg with full filter/mask structure."""
import re
from pathlib import Path

WIDTH = 562
LEFT = Path(__file__).resolve().parents[1] / "frontend/src/assets/design/chrome/bottom-rail-left.svg"
RIGHT = Path(__file__).resolve().parents[1] / "frontend/src/assets/design/chrome/bottom-rail-right.svg"


def mirror_x(x: float) -> float:
    return WIDTH - x


def mirror_path(d: str) -> str:
    tokens = re.findall(r"[A-Za-z]|-?\d*\.?\d+(?:e[-+]?\d+)?", d)
    out: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not re.match(r"^[A-Za-z]$", token):
            i += 1
            continue
        cmd = token
        out.append(cmd)
        i += 1
        nums: list[float] = []
        while i < len(tokens) and not re.match(r"^[A-Za-z]$", tokens[i]):
            nums.append(float(tokens[i]))
            i += 1
        if cmd in "MmLl":
            for j in range(0, len(nums), 2):
                nums[j] = mirror_x(nums[j])
        elif cmd in "Hh":
            for j in range(len(nums)):
                nums[j] = mirror_x(nums[j])
        for n in nums:
            if n == int(n):
                out.append(str(int(n)))
            else:
                out.append(format(n, ".4f").rstrip("0").rstrip("."))
    return " ".join(out)


def mirror_linear_gradient(attrs: str) -> str:
    def repl(m: re.Match[str]) -> str:
        key, val = m.group(1), float(m.group(2))
        if key in ("x1", "x2"):
            val = mirror_x(val)
        if val == int(val):
            return f'{key}="{int(val)}"'
        return f'{key}="{val}"'

    return re.sub(r'(x1|x2|y1|y2)="(-?\d*\.?\d+)"', repl, attrs)


def mirror_filter(attrs: str) -> str:
    def repl(m: re.Match[str]) -> str:
        key, val = m.group(1), float(m.group(2))
        if key == "x":
            val = mirror_x(val + float(re.search(r'width="(\d+)"', attrs).group(1))) - float(
                re.search(r'width="(\d+)"', attrs).group(1)
            )
        return f'{key}="{int(val)}"'

    return attrs


def main() -> None:
    text = LEFT.read_text(encoding="utf-8")
    text = text.replace("16_148", "16_149")

    text = re.sub(r'd="([^"]+)"', lambda m: f'd="{mirror_path(m.group(1))}"', text)

    def grad_repl(m: re.Match[str]) -> str:
        return f"<linearGradient {mirror_linear_gradient(m.group(1))}>"

    text = re.sub(r"<linearGradient ([^>]+)>", grad_repl, text)

    # mask rects: mirror x offset for mask0 (width 561)
    text = re.sub(
        r'(<mask id="mask0_16_149"[^>]+x=")0(" width="561")',
        r"\g<1>1\2",
        text,
    )

    RIGHT.write_text(text, encoding="utf-8")
    print(f"Wrote {RIGHT}")


if __name__ == "__main__":
    main()
