"""Render docs/images/architecture.png (data flow diagram). Requires matplotlib."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "images" / "architecture.png"

COLORS = {
    "source": "#E8F1FB", "ingest": "#EAF6EC", "store": "#FFF6E0", "ai": "#F3EAFB", "out": "#FDECEC",
    "control": "#F2F2F2",
}
EDGE = "#3A3A3A"


def box(ax, x, y, w, h, title, body, kind, align="center"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12",
                                fc=COLORS[kind], ec=EDGE, lw=1.2))
    ax.text(x + w / 2, y + h - 0.22, title, ha="center", va="top", fontsize=10.5, weight="bold")
    bx = x + w / 2 if align == "center" else x + 0.35
    ax.text(bx, y + h - 0.58, body, ha=align, va="top", fontsize=8.3, linespacing=1.35)


def arrow(ax, x1, y1, x2, y2, label=None, dashed=False):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=14, lw=1.3,
                                 color=EDGE, linestyle="--" if dashed else "-"))
    if label:
        ax.text((x1 + x2) / 2 + 0.08, (y1 + y2) / 2, label, fontsize=7.6, color="#444", va="center")


def main():
    fig, ax = plt.subplots(figsize=(13, 10.5))
    ax.set_xlim(0, 13)
    ax.set_ylim(0, 10.5)
    ax.axis("off")
    ax.text(6.5, 10.25, "Data Catalog Search Assistant - architecture & data flow", ha="center",
            fontsize=14, weight="bold")

    # sources
    box(ax, 0.4, 8.4, 5.6, 1.5, "Source warehouse (synthetic)",
        "7 SQLite files = 7 schemas, 16 tables\nsales, product, marketing, finance,\nhr, support, analytics", "source")
    box(ax, 7.0, 8.4, 5.6, 1.5, "Steward metadata (CSV, layered dirs)",
        "business_tables / business_columns\nglossary / access_roles / user_roles\n+ change batch 002", "source")
    # ingestion
    box(ax, 0.4, 6.75, 5.6, 1.25, "Harvester (Python)",
        "read-only PRAGMA table_info, row counts,\nnull fraction, distinct counts", "ingest")
    box(ax, 7.0, 6.75, 5.6, 1.25, "CSV loader (Python)",
        "file/header contract (fail run on drift),\nrows stored as received (TEXT)", "ingest")
    arrow(ax, 3.2, 8.4, 3.2, 8.0)
    arrow(ax, 9.8, 8.4, 9.8, 8.0)
    # raw -> staging -> curated
    box(ax, 0.4, 5.1, 12.2, 1.25, "Raw layer  (raw_*, append-only, keyed by run_id)",
        "fingerprint of inputs: unchanged -> run 'skipped' (idempotent rerun)   |   whole run = one transaction",
        "store")
    arrow(ax, 3.2, 6.75, 3.2, 6.35)
    arrow(ax, 9.8, 6.75, 9.8, 6.35)
    box(ax, 0.4, 3.45, 5.6, 1.3, "Staging + validation (SQL)",
        "trim/lower, flag parsing, identifier checks,\nunknown roles, orphans, in-file duplicates\n(ROW_NUMBER) -> quarantine + reason", "ingest")
    box(ax, 7.0, 3.45, 5.6, 1.3, "Curated catalog (SQL UPSERT)",
        "catalog_table / catalog_column / glossary\nrole_grant + views  |  change_log diff\nfail-closed: unclassified -> restricted, PII -> pii", "store")
    arrow(ax, 3.2, 5.1, 3.2, 4.75)
    arrow(ax, 6.0, 4.1, 7.0, 4.1)
    # search index
    box(ax, 7.0, 1.95, 5.6, 1.15, "Search index (FTS5, BM25)",
        "table + column docs with access roles;\nonly changed tables re-indexed", "store")
    arrow(ax, 9.8, 3.45, 9.8, 3.1)
    # assistant
    box(ax, 0.4, 0.25, 5.6, 2.85, "Assistant (question + role)",
        "1. AI query rewrite (optional, validated)\n2. role-filtered retrieval in SQL\n   = access boundary\n"
        "3. no evidence -> 'no match' (AI not called)\n4. AI picks datasets from evidence\n"
        "5. validator: tables/columns must be in\n   evidence, else deterministic fallback", "ai", align="left")
    arrow(ax, 7.0, 2.5, 6.0, 2.5, "evidence")
    box(ax, 7.0, 0.6, 2.65, 1.2, "LLMClient interface",
        "Ollama (local) |\nOpenAI-compatible\n(Groq, Gemini) | offline", "ai")
    box(ax, 9.95, 0.6, 2.65, 1.2, "Outputs",
        "CLI, Streamlit UI\nsearch_log, eval report\nrun / quarantine views", "out")
    ax.add_patch(FancyArrowPatch((6.0, 1.3), (7.0, 1.3), arrowstyle="<|-|>", mutation_scale=14, lw=1.3,
                                 color=EDGE, linestyle="--"))
    ax.text(6.05, 1.45, "prompt / JSON", fontsize=7.6, color="#444")
    ax.plot([6.0, 11.27], [0.4, 0.4], color=EDGE, lw=1.3)
    arrow(ax, 11.27, 0.4, 11.27, 0.6)
    ax.text(7.0, 0.27, "answer + evidence + validation status", fontsize=7.6, color="#444")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=140, bbox_inches="tight", facecolor="white")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
