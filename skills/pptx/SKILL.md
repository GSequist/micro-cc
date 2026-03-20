---
name: pptx
description: "Use this skill any time a .pptx file is involved in any way — as input, output, or both. This includes: creating slide decks, pitch decks, or presentations; reading, parsing, or extracting text from any .pptx file; editing, modifying, or updating existing presentations; combining or splitting slide files; working with templates, layouts, speaker notes, or comments. Trigger whenever the user mentions \"deck,\" \"slides,\" \"presentation,\" or references a .pptx filename."
---

# PPTX Skill

## Quick Reference

| Task | Guide |
|------|-------|
| Read/analyze content | `python -m markitdown presentation.pptx` |
| Edit or create from template | Read [editing.md](editing.md) |
| Create from scratch | Read [pptxgenjs.md](pptxgenjs.md) |

## Scripts

All scripts live in the skill's `scripts/` directory. Use `list_skills` or `read_skill` to get the skill path, then run scripts relative to it.

| Script | Path | Purpose |
|--------|------|---------|
| `unpack.py` | `scripts/office/unpack.py` | Extract and pretty-print PPTX |
| `pack.py` | `scripts/office/pack.py` | Repack with validation |
| `validate.py` | `scripts/office/validate.py` | Validate OOXML against schemas |
| `add_slide.py` | `scripts/add_slide.py` | Duplicate slide or create from layout |
| `clean.py` | `scripts/clean.py` | Remove orphaned files |
| `thumbnail.py` | `scripts/thumbnail.py` | Create visual grid of slides |
| `soffice.py` | `scripts/office/soffice.py` | LibreOffice wrapper (PDF conversion) |

---

## Reading Content

```bash
# Text extraction
python -m markitdown presentation.pptx

# Visual overview
python scripts/thumbnail.py presentation.pptx

# Raw XML
python scripts/office/unpack.py presentation.pptx unpacked/
```

---

## Editing Workflow

**Read [editing.md](editing.md) for full details.**

1. Analyze template with `thumbnail.py`
2. Unpack → manipulate slides → edit content → clean → pack

---

## Creating from Scratch

**Read [pptxgenjs.md](pptxgenjs.md) for full details.**

Use when no template or reference presentation is available.

---

## Branding (Ask First)

**Before creating any presentation, ask the user for their brand guidelines.** Do NOT assume colors, fonts, or style. Gather:

1. **Colors** — primary, secondary, accent hex codes. Which is for text? Backgrounds? Accents?
2. **Fonts** — heading font and body font (e.g. Georgia + Arial, or whatever they use)
3. **Logo** — file path or URL if they want it on slides; placement preference (corner, title slide only, etc.)
4. **Template** — do they have an existing `.pptx` template to build from? If yes, use the template-based editing workflow and let the template's master slides drive styling.
5. **Style** — corporate-minimal? Bold and colorful? Dark theme? Ask if unclear.

If the user says "just make it look good" without brand specs, pick a cohesive palette from the Design Ideas section below and state your choices so they can course-correct.

Once brand is established, apply it **consistently across every slide** — never mix branded and unbranded slides.

---

## Design Ideas

**Don't create boring slides.** Plain bullets on a white background won't impress anyone. Consider ideas from this list for each slide — while staying within the user's brand.

### For Each Slide

**Every slide needs a visual element** — image, chart, icon, or shape. Text-only slides are forgettable.

**Layout options:**
- Two-column (text left, illustration on right)
- Icon + text rows (icon in accent-colored circle, bold header, description below)
- 2x2 or 2x3 grid (image on one side, grid of content blocks on other)
- Half-bleed image with content overlay

**Data display:**
- Large stat callouts (big numbers 60-72pt in accent color with small labels below)
- Comparison columns (before/after, pros/cons, side-by-side options)
- Timeline or process flow (numbered steps, arrows)

**Visual polish:**
- Accent-colored bar on left edge of content cards
- Thin accent separator lines between sections
- Icons in small accent-colored circles next to section headers

### Spacing

- 0.5" minimum margins
- 0.3-0.5" between content blocks
- Leave breathing room — don't fill every inch

### Avoid (Common Mistakes)

- **Don't repeat the same layout** — vary columns, cards, and callouts across slides
- **Don't center body text** — left-align paragraphs and lists; center only titles
- **Don't skimp on size contrast** — titles need 28pt+ to stand out from 14-16pt body
- **Don't mix spacing randomly** — choose 0.3" or 0.5" gaps and use consistently
- **Don't style one slide and leave the rest plain** — commit fully or keep it simple throughout
- **Don't create text-only slides** — add images, icons, charts, or visual elements
- **Don't forget text box padding** — when aligning lines or shapes with text edges, set `margin: 0` on the text box or offset the shape to account for padding
- **Don't use low-contrast elements** — icons AND text need strong contrast against the background
- **NEVER use accent lines under titles** — use whitespace or background color instead

---

## QA (Required)

**Assume there are problems. Your job is to find them.**

Your first render is almost never correct. Approach QA as a bug hunt, not a confirmation step.

### Content QA

```bash
python -m markitdown output.pptx
```

Check for missing content, typos, wrong order.

**When using templates, check for leftover placeholder text:**

```bash
python -m markitdown output.pptx | grep -iE "xxxx|lorem|ipsum|this.*(page|slide).*layout"
```

### Visual QA

Convert slides to images (see [Converting to Images](#converting-to-images)), then inspect each slide for:
- Overlapping elements (text through shapes, lines through words)
- Text overflow or cut off at edges/box boundaries
- Elements too close (< 0.3" gaps) or cards/sections nearly touching
- Uneven gaps (large empty area in one place, cramped in another)
- Insufficient margin from slide edges (< 0.5")
- Columns or similar elements not aligned consistently
- Low-contrast text or icons
- Text boxes too narrow causing excessive wrapping
- Leftover placeholder content
- **Brand violations**: wrong fonts, wrong colors, inconsistent accent usage

### Verification Loop

1. Generate slides → Convert to images → Inspect
2. **List issues found** (if none found, look again more critically)
3. Fix issues
4. **Re-verify affected slides** — one fix often creates another problem
5. Repeat until a full pass reveals no new issues

---

## Converting to Images

```bash
python scripts/office/soffice.py --headless --convert-to pdf output.pptx
pdftoppm -jpeg -r 150 output.pdf slide
```

Creates `slide-01.jpg`, `slide-02.jpg`, etc.

To re-render specific slides after fixes:

```bash
pdftoppm -jpeg -r 150 -f N -l N output.pdf slide-fixed
```

---

## Dependencies

- `pip install "markitdown[pptx]"` - text extraction
- `pip install Pillow` - thumbnail grids
- `npm install -g pptxgenjs` - creating from scratch
- LibreOffice (`soffice`) - PDF conversion (auto-configured via `scripts/office/soffice.py`)
- Poppler (`pdftoppm`) - PDF to images
