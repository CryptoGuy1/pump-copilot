"""The README's key results, written from the stored results (never typed in), so they cannot
drift: `pumpcopilot readme` rewrites the block between the markers, and a test checks that the
committed README equals what the stored results say."""

from __future__ import annotations

from pathlib import Path

from . import chapters

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
START = ("<!-- key results: written by `pumpcopilot readme` from the stored results; "
         "do not edit by hand -->")
END = "<!-- /key results -->"
DEMO = "https://cryptoguy1.github.io/pump-copilot/"


def results_block(reports: Path = ROOT / "reports") -> str:
    c = chapters.chapters(reports)
    z, cira, a = c["zema"], c["cira"], c["assistant"]
    chrono = next(s for s in z["splits"] if s["split"] == "chronological")
    h = chrono["headline"]
    blind = [r for r in a["revisions"] if r["holdout_commit"]]
    final = a["revisions"][-1]
    adv = final["adversarial"]
    corrected = (f" ({adv['final_passed_corrected']}/{adv['total']} under the corrected "
                 "expectations)" if adv.get("final_passed_corrected") is not None else "")
    holdouts = " and ".join(
        f"{r['questions']['served_checked']} of {r['questions']['total']} (checker revision "
        f"{r['checker_version']})" for r in blind)
    lines = [
        START,
        f"- **ZeMA hydraulic test rig.** {z['contrast']} (95% interval "
        f"{h['lo']:.3f}–{h['hi']:.3f}; always guessing scores "
        f"{chrono['majority']['macro_f1']:.3f}). Test rig only: it says nothing about the CIRA "
        f"pumps. [Evaluation]({DEMO}evaluation) · [report]({z['report']})",
        f"- **CIRA detector.** {cira['key_finding']} [Evaluation]({DEMO}evaluation) · "
        f"[report]({cira['report']})",
        f"- **Assistant.** On questions written blind before each run, the checked model's "
        f"answer was served for {holdouts}; the others fell back to the evidence summary. On "
        f"{adv['total']} adversarial prompts with the final checker, {adv['final_passed']}/"
        f"{adv['total']} final answers met their registered expectations{corrected}. "
        f"**{a['instructions_served']} control instructions served** in the "
        f"{a['served_answers']} answers the model served. [Evaluation]({DEMO}evaluation) · "
        f"[report]({a['report']})",
        END,
    ]
    return "\n".join(lines)


def current_block(text: str) -> str:
    i, j = text.index(START), text.index(END) + len(END)
    return text[i:j]


def update(readme: Path = README, reports: Path = ROOT / "reports") -> bool:
    """Rewrite the block; True if it changed."""
    text = readme.read_text()
    new = text.replace(current_block(text), results_block(reports))
    if new != text:
        readme.write_text(new)
    return new != text
