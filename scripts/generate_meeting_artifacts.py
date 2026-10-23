"""Generate the Zava FinOps Customer Meeting PowerPoint Deck (.pptx) and Executive Leave-Behind (.docx)."""

from pathlib import Path
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE
import docx
from docx.shared import Inches as DocxInches, Pt as DocxPt, RGBColor as DocxRGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


ROOT = Path(__file__).resolve().parent.parent
SCREENSHOTS = ROOT / "artifacts" / "screenshots" / "operating-demo"
PPTX_OUT = ROOT / "artifacts" / "Zava-FinOps-Customer-Presentation.pptx"
DOCX_OUT = ROOT / "artifacts" / "Zava-FinOps-Architecture-and-CSA-Charter.docx"

# Palette
C_DARK_NAVY = RGBColor(0x14, 0x23, 0x3C)
C_TEAL = RGBColor(0x00, 0x78, 0x6B)
C_BLUE = RGBColor(0x00, 0x6D, 0xAA)
C_RUST = RGBColor(0xB9, 0x4A, 0x32)
C_AMBER = RGBColor(0x8A, 0x57, 0x00)
C_CHARCOAL = RGBColor(0x24, 0x24, 0x24)
C_MUTED = RGBColor(0x59, 0x59, 0x59)
C_BG_LIGHT = RGBColor(0xF3, 0xF4, 0xF5)
C_WHITE = RGBColor(0xFF, 0xFF, 0xFF)
C_BORDER = RGBColor(0xD8, 0xDE, 0xE4)


def set_slide_bg(slide, color):
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = color


def add_header(slide, title_text, subtitle_text, dark=False):
    # Top accent bar
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.333), Inches(0.1))
    bar.fill.solid()
    bar.fill.fore_color.rgb = C_TEAL
    bar.line.fill.background()

    tb = slide.shapes.add_textbox(Inches(0.5), Inches(0.22), Inches(12.333), Inches(0.85))
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = title_text
    p.font.name = "Segoe UI Semibold"
    p.font.size = Pt(24)
    p.font.bold = True
    p.font.color.rgb = C_WHITE if dark else C_CHARCOAL

    p2 = tf.add_paragraph()
    p2.text = subtitle_text
    p2.font.name = "Segoe UI"
    p2.font.size = Pt(11.5)
    p2.font.color.rgb = RGBColor(0xCC, 0xD6, 0xE0) if dark else C_MUTED


def add_card(slide, left, top, width, height, title, big_val, sub_text, accent=C_TEAL):
    card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(height))
    card.fill.solid()
    card.fill.fore_color.rgb = C_WHITE
    card.line.color.rgb = C_BORDER
    card.line.width = Pt(1)

    stripe = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(0.08), Inches(height))
    stripe.fill.solid()
    stripe.fill.fore_color.rgb = accent
    stripe.line.fill.background()

    tb = slide.shapes.add_textbox(Inches(left + 0.16), Inches(top + 0.08), Inches(width - 0.24), Inches(height - 0.16))
    tf = tb.text_frame
    tf.word_wrap = True
    p0 = tf.paragraphs[0]
    p0.text = title
    p0.font.name = "Segoe UI Semibold"
    p0.font.size = Pt(11)
    p0.font.bold = True
    p0.font.color.rgb = C_MUTED

    if big_val:
        p1 = tf.add_paragraph()
        p1.text = big_val
        p1.font.name = "Segoe UI Semibold"
        p1.font.size = Pt(22)
        p1.font.bold = True
        p1.font.color.rgb = accent

    p2 = tf.add_paragraph()
    p2.text = sub_text
    p2.font.name = "Segoe UI"
    p2.font.size = Pt(10)
    p2.font.color.rgb = C_CHARCOAL


def add_screenshot_slide(prs, title, subtitle, img_name, callouts):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide, C_BG_LIGHT)
    add_header(slide, title, subtitle)

    # Left column callout cards
    y = 1.18
    for c_title, c_val, c_sub, c_color in callouts:
        add_card(slide, 0.5, y, 3.85, 1.38, c_title, c_val, c_sub, c_color)
        y += 1.48

    # Right side screenshot frame
    frame = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(4.55), Inches(1.18), Inches(8.3), Inches(5.8))
    frame.fill.solid()
    frame.fill.fore_color.rgb = C_WHITE
    frame.line.color.rgb = C_BORDER
    frame.line.width = Pt(1.5)

    img_path = SCREENSHOTS / img_name
    if img_path.exists():
        slide.shapes.add_picture(str(img_path), Inches(4.62), Inches(1.25), width=Inches(8.16))


def add_arch_node(slide, left, top, width, height, badge, title, body, accent, bg_color=C_WHITE, dashed=False):
    box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(height))
    box.fill.solid()
    box.fill.fore_color.rgb = bg_color
    box.line.color.rgb = accent if dashed else C_BORDER
    box.line.width = Pt(1.5 if dashed else 1.0)

    # Top header pill inside node
    hdr = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(0.28))
    hdr.fill.solid()
    hdr.fill.fore_color.rgb = accent
    hdr.line.fill.background()

    htb = slide.shapes.add_textbox(Inches(left + 0.08), Inches(top + 0.02), Inches(width - 0.16), Inches(0.24))
    htf = htb.text_frame
    htf.word_wrap = False
    hp = htf.paragraphs[0]
    hp.text = badge
    hp.font.name = "Segoe UI Semibold"
    hp.font.size = Pt(8.5)
    hp.font.bold = True
    hp.font.color.rgb = C_WHITE

    btb = slide.shapes.add_textbox(Inches(left + 0.1), Inches(top + 0.30), Inches(width - 0.2), Inches(height - 0.34))
    btf = btb.text_frame
    btf.word_wrap = True
    bp1 = btf.paragraphs[0]
    bp1.text = title
    bp1.font.name = "Segoe UI Semibold"
    bp1.font.size = Pt(10)
    bp1.font.bold = True
    bp1.font.color.rgb = C_CHARCOAL

    bp2 = btf.add_paragraph()
    bp2.text = body
    bp2.font.name = "Segoe UI"
    bp2.font.size = Pt(8.5)
    bp2.font.color.rgb = C_MUTED


def add_architecture_slide(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide, C_BG_LIGHT)
    add_header(
        slide,
        "Zava FinOps & Observability Platform — Architecture & Marketecture",
        "What is built & verified today (solid lanes) + Phase 2/3 real-time anomaly detection & Operations Agent HOL automation (bottom lane)"
    )

    # 4 Column Stage Banners (What's Built Today)
    stages = [
        (0.4, 2.85, "1. AUTHORITATIVE SOURCES", C_DARK_NAVY),
        (3.45, 3.05, "2. ONELAKE MEDALLION (F4)", C_TEAL),
        (6.70, 3.05, "3. DIRECT LAKE & AI SERVING", C_BLUE),
        (9.95, 2.98, "4. EXPERIENCE & WRITEBACK", C_RUST),
    ]
    for sx, sw, stitle, scol in stages:
        banner = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(sx), Inches(1.15), Inches(sw), Inches(0.34))
        banner.fill.solid()
        banner.fill.fore_color.rgb = scol
        banner.line.fill.background()
        tf = banner.text_frame
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        p.text = stitle
        p.font.name = "Segoe UI Semibold"
        p.font.size = Pt(9.5)
        p.font.bold = True
        p.font.color.rgb = C_WHITE

    # Arrows between stage banners
    for ax in (3.27, 6.52, 9.77):
        arr = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(ax), Inches(1.22), Inches(0.16), Inches(0.20))
        arr.fill.solid()
        arr.fill.fore_color.rgb = C_MUTED
        arr.line.fill.background()

    # Column 1: Sources
    add_arch_node(
        slide, 0.4, 1.56, 2.85, 1.15,
        "BUILT  |  FINANCIAL TRUTH",
        "Azure Cost Exports & Native API",
        "• Legacy ActualCost (65 cols) & FOCUS 1.0r2\n• 12 closed months | 30,078 charge rows\n• Native Cost Query API reconciliation",
        C_TEAL
    )
    add_arch_node(
        slide, 0.4, 2.78, 2.85, 1.15,
        "BUILT  |  DATABRICKS & UC",
        "Databricks System Tables",
        "• system.billing.usage (504.86 DBUs)\n• system.query.history (4,526 statements)\n• information_schema (262 UC objects)",
        C_RUST
    )
    add_arch_node(
        slide, 0.4, 4.00, 2.85, 1.15,
        "BUILT  |  FABRIC & OWNERSHIP",
        "FUAM Lakehouse & Context SQL",
        "• FUAM: 532 items | 39 workspaces | 1.02M CUs\n• ZavaFinOpsBusinessContext SQL DB\n• ARM Microsoft.Resources/tags",
        C_BLUE
    )

    # Column 2: OneLake Medallion
    add_arch_node(
        slide, 3.45, 1.56, 3.05, 1.15,
        "BUILT  |  BRONZE LAYER",
        "Content-Addressed Raw Evidence",
        "• Immutable raw Parquets & CLI JSON pages\n• SHA-256 content & schema digests\n• Extraction SQL & row-count receipts",
        C_DARK_NAVY
    )
    add_arch_node(
        slide, 3.45, 2.78, 3.05, 1.15,
        "BUILT  |  SILVER LAYER",
        "High-Precision Canonical Tables",
        "• Financial: Decimal(38,28) signed charges\n• Databricks: Decimal(38,18) DBU & SCD warehouses\n• Zero value-based row deduplication",
        C_TEAL
    )
    add_arch_node(
        slide, 3.45, 4.00, 3.05, 1.15,
        "BUILT  |  GOLD & SERVING (v7)",
        "29 Versioned Delta Tables",
        "• demo.*_2d98f495bc56 in ZavaFinOpsFoundation\n• Conformed Resource (854), Service, Calendar\n• Single-executor Spark (F4 cost discipline)",
        C_TEAL
    )

    # Column 3: Direct Lake & AI
    add_arch_node(
        slide, 6.70, 1.56, 3.05, 1.78,
        "BUILT  |  SEMANTIC LAYER",
        "ZavaFinOpsDemo (Direct Lake)",
        "• 29 tables | 23 1-way relationships | 62 measures\n• DirectLakeOnly mode on OneLake Delta\n• Explicit 4-decimal rounding bridge (-$0.0861)\n• $0.0000 unexplained variance across 12 months\n• Strict DBU / CU / USD unit isolation",
        C_BLUE
    )
    add_arch_node(
        slide, 6.70, 3.42, 3.05, 1.73,
        "BUILT  |  CONVERSATIONAL BI",
        "ZavaFinOps_Agent (Data Agent)",
        "• Grounded across all 29 tables & 62 measures\n• Domain AI instructions for FOCUS, UC & FUAM\n• 3-month rolling-origin forecast & MAE backtest\n• Strictly read-only (refuses unverified query/job $ or tag mutations)",
        C_DARK_NAVY
    )

    # Column 4: Experience & Writeback
    add_arch_node(
        slide, 9.95, 1.56, 2.98, 1.15,
        "BUILT  |  7-PAGE REPORT & APP",
        "Zava FinOps Operating Demo",
        "• 1. Overview | 2. Databricks | 3. Fabric FUAM\n• 4. Usage | 5. Anomalies & Advisor Insights\n• 6. Governance & Action | 7. Planning + App",
        C_BLUE
    )
    add_arch_node(
        slide, 9.95, 2.78, 2.98, 1.15,
        "BUILT  |  NATIVE AZURE TAGGING",
        "Power Automate + Scoped RBAC",
        "• Interactive Tag Key & Value textSlicers\n• Flow 619b22a5 -> ARM PATCH Merge\n• Custom role: Zava FinOps Demo Tag Operator",
        C_RUST
    )
    add_arch_node(
        slide, 9.95, 4.00, 2.98, 1.15,
        "BUILT  |  INTERNAL CONTEXT",
        "Fabric UDF + SQL Audit Ledger",
        "• AssignBusinessContext Python UDF\n• Caller Entra OID + optimistic version check\n• Effective-dated historical cost attribution",
        C_AMBER
    )

    # Bottom Horizon Band: Future Work (Real-Time Anomaly Detection & Operations Agents for HOL Automations)
    future_banner = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.4), Inches(5.30), Inches(12.53), Inches(0.32))
    future_banner.fill.solid()
    future_banner.fill.fore_color.rgb = C_AMBER
    future_banner.line.fill.background()
    ftf = future_banner.text_frame
    fp = ftf.paragraphs[0]
    fp.alignment = PP_ALIGN.CENTER
    fp.text = "NEXT HORIZON (CSA PHASE 3–4 ROADMAP): REAL-TIME ANOMALY DETECTION, FOUNDRY/M365 TELEMETRY & OPERATIONS AGENT HOL AUTOMATIONS"
    fp.font.name = "Segoe UI Semibold"
    fp.font.size = Pt(9.5)
    fp.font.bold = True
    fp.font.color.rgb = C_WHITE

    future_bg = RGBColor(0xFF, 0xFB, 0xF2)
    add_arch_node(
        slide, 0.4, 5.68, 4.05, 1.52,
        "FUTURE  |  REAL-TIME STREAMING & ANOMALY DETECTION",
        "Eventstream + Eventhouse KQL + Activator",
        "• Live Fabric Capacity Events, Azure Monitor & Databricks logs via Fabric Eventstream into Eventhouse (KQL DB).\n• Continuous KQL anomaly detection on CU/DBU burn spikes, runaway queries, and capacity throttling.\n• Sub-minute Data Activator (Reflex) alerts before invoice shock.",
        C_AMBER, bg_color=future_bg, dashed=True
    )
    add_arch_node(
        slide, 4.64, 5.68, 4.05, 1.52,
        "FUTURE  |  AI & SAAS UNIT ECONOMICS",
        "Microsoft Foundry (Azure AI) & M365 Copilot",
        "• Ingest Azure AI Foundry token/deployment telemetry, PTU vs. PAYG utilization, and model inference costs.\n• Onboard Microsoft Graph M365 Copilot seat adoption & utilization feeds.\n• Map AI unit economics ($/1K tokens, $/active user) to Business Units.",
        C_BLUE, bg_color=future_bg, dashed=True
    )
    add_arch_node(
        slide, 8.88, 5.68, 4.05, 1.52,
        "FUTURE  |  OPERATIONS AGENTS & HOL WORKFLOWS",
        "Human-on-the-Loop (HOL) Autonomous Remediation",
        "• Fabric Operations Agent monitors real-time KQL anomaly & idle-capacity signals.\n• Drafts remediation payloads (pause idle capacity, right-size warehouse, apply missing tags, quarantine job).\n• Routes Teams Adaptive Card for 1-click HOL approval -> executes via scoped Logic Apps / Power Automate.",
        C_RUST, bg_color=future_bg, dashed=True
    )


def build_pptx():
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    # Slide 1: Dark Title Slide
    s1 = prs.slides.add_slide(blank)
    set_slide_bg(s1, C_DARK_NAVY)
    bar = s1.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.333), Inches(0.14))
    bar.fill.solid()
    bar.fill.fore_color.rgb = C_TEAL
    bar.line.fill.background()

    tb = s1.shapes.add_textbox(Inches(0.8), Inches(1.3), Inches(11.7), Inches(3.2))
    tf = tb.text_frame
    tf.word_wrap = True
    p0 = tf.paragraphs[0]
    p0.text = "ZAVA ENTERPRISE FINOPS & OBSERVABILITY"
    p0.font.name = "Segoe UI Semibold"
    p0.font.size = Pt(14)
    p0.font.color.rgb = RGBColor(0x4D, 0xD0, 0xE1)

    p1 = tf.add_paragraph()
    p1.text = "Customer-Owned FinOps, Platform Observability\n& Governed Action on Microsoft Fabric"
    p1.font.name = "Segoe UI Semibold"
    p1.font.size = Pt(36)
    p1.font.bold = True
    p1.font.color.rgb = C_WHITE

    p2 = tf.add_paragraph()
    p2.text = "Retiring Black-Box Cost Tools with Reconciled FOCUS Billing, Databricks & FUAM System Tables, Bi-Directional Tagging & Grounded AI"
    p2.font.name = "Segoe UI"
    p2.font.size = Pt(15)
    p2.font.color.rgb = RGBColor(0xCC, 0xD6, 0xE0)

    metrics = [
        ("12 Months Reconciled", "$16,919.83", "30,078 charge rows | $0.00 unexplained variance", C_TEAL),
        ("Databricks & UC Estate", "504.86 DBUs", "262 UC assets | 29 schemas | 4,526 queries", C_RUST),
        ("Fabric FUAM Estate", "1,021,022 CUs", "532 active items | 39 workspaces | 5 capacities", C_BLUE),
        ("Governed Action & AI", "854 Resources", "Live ARM Tag Merge + UDF + ZavaFinOps_Agent", C_AMBER),
    ]
    x = 0.8
    for m_title, m_val, m_sub, m_col in metrics:
        add_card(s1, x, 4.85, 2.8, 1.85, m_title, m_val, m_sub, m_col)
        x += 2.98

    # Slide 2: Strategic Imperative & Architecture
    s2 = prs.slides.add_slide(blank)
    set_slide_bg(s2, C_BG_LIGHT)
    add_header(s2, "Why Customer-Owned FinOps on Fabric Beats Legacy SaaS Tools",
               "Moving from passive invoice dashboards (Apptio) to unified financial truth, inside-the-capacity observability, and native action")

    pillars = [
        ("1. Authoritative Financial Truth", "$0.00 Unexplained",
         "• Ingests 65-col ActualCost & 96-col FOCUS 1.0r2 exports into OneLake.\n• Preserves Decimal(38,28) source precision with an explicit 4-decimal rounding bridge (-$0.0861).\n• Reconciles every month against native Azure Cost Management queries.", C_TEAL),
        ("2. Deep Platform Observability", "DBUs + CUs + Catalogs",
         "• Joins Unity Catalog (262 assets, 29 schemas) & Databricks system.billing.usage / query.history.\n• Integrates Fabric FUAM (532 items, 39 workspaces, 1.02M CUs).\n• Strictly separates DBUs and CUs from billed USD—zero double counting.", C_BLUE),
        ("3. Bi-Directional Governance", "Native Writeback",
         "• Mode A: Report-triggered Power Automate flow executing ARM PATCH Merge via least-privilege custom RBAC.\n• Mode B: Effective-dated business context via Fabric UDF & SQL with optimistic concurrency.\n• Full caller vs. execution identity audit trail.", C_RUST),
        ("4. Selective FinOps Hub & AI", "Cost-Conscious F4",
         "• Evaluates FinOps Toolkit v14 selectively rather than forcing heavy ADF/ADX templates.\n• Runs on existing Fabric F4 capacity with single-executor Spark & Direct Lake.\n• Grounded read-only ZavaFinOps_Agent over all 27 tables & 53 measures.", C_AMBER),
    ]
    coords = [(0.5, 1.25), (6.75, 1.25), (0.5, 4.25), (6.75, 4.25)]
    for (lx, ly), (ptitle, pval, pbody, pcol) in zip(coords, pillars):
        add_card(s2, lx, ly, 6.05, 2.75, ptitle, pval, pbody, pcol)

    # Slide 3: Visual Architecture / Marketecture Slide (What's Built + Future Real-Time & Operations Agent HOL)
    add_architecture_slide(prs)

    # Slide 4: Page 1 - Overview & Trust
    add_screenshot_slide(
        prs,
        "Act 1: Executive Financial Trust & Row-Level Provenance",
        "Page 1 (Overview & Trust) — 12 closed months reconciled with 30,078 row-level charges and 854 billed resources",
        "Overview & Trust.png",
        [
            ("12-Month Billed Spend", "$16,919.83", "Exact source: $16,919.9178 | Explicit rounding bridge: -$0.0861 | Unexplained: $0.0000", C_TEAL),
            ("Unassigned Visibility", "68.4% ($11,570)", "Surfaces unallocated spend honestly rather than hiding unknown ownership", C_AMBER),
            ("Row-Level Charge Ledger", "30,078 Records", "Drill directly into Date, Resource, Service, Charge Category, and Exact Cost", C_BLUE),
            ("One-Click AI Assistant", "ZavaFinOps_Agent", "Launch grounded natural-language Q&A directly from the top-right header bar", C_DARK_NAVY),
        ]
    )

    # Slide 4: Page 2 - Databricks & UC System Tables
    add_screenshot_slide(
        prs,
        "Act 2: Azure Databricks & Unity Catalog System Tables",
        "Page 2 (Databricks & UC System Tables) — Lakeview-grade observability powered by system.information_schema, billing.usage & query.history",
        "Databricks & UC System Tables.png",
        [
            ("Live Unity Catalog Estate", "29 Schemas | 259 Tables", "3 Catalogs (samples, system, demo) + 3 Managed Volumes = 262 total objects", C_BLUE),
            ("Format & Type Mix", "55.2% MANAGED", "106 DELTA tables, 64 Views, 51 Unity Catalog objects, 38 Delta Sharing tables", C_TEAL),
            ("Workload Product Share", "504.86 DBUs", "SQL (70.66%), Predictive Optimization (25.88%), Interactive (3.47%)", C_RUST),
            ("Daily SKU Burn Area Chart", "4 Compute SKUs", "Tracks daily DBU consumption across Serverless SQL, Pro SQL, Jobs & All-Purpose", C_AMBER),
        ]
    )

    # Slide 5: Page 3 - Fabric & FUAM System Tables
    add_screenshot_slide(
        prs,
        "Act 3: Microsoft Fabric Estate & FUAM Capacity Telemetry",
        "Page 3 (Fabric & FUAM System Tables) — 1:1 visual symmetry with Databricks across 39 workspaces and 532 active items",
        "Fabric & FUAM System Tables.png",
        [
            ("Fabric Estate Inventory", "39 Workspaces | 532 Items", "5 capacity scopes (F16, F8, F4, F2, PP3) extracted from FUAM_Lakehouse", C_TEAL),
            ("Artifact Type Distribution", "31 Item Types", "117 Notebooks, 67 Semantic Models, 67 SQL Endpoints, 50 Lakehouses, 33 Reports", C_BLUE),
            ("Capacity Unit Telemetry", "1,021,021.69 CUs", "Aug 17–28 FUAM slice: 47,530 operations across Dataset, Lakehouse & Notebook kinds", C_RUST),
            ("Workload Domain Mix", "6 Fabric Domains", "DW & Lakehouse (27.3%), Data Eng & AI (22.9%), Power BI (19.6%), RTI (14.5%)", C_AMBER),
        ]
    )

    # Slide 6: Page 4 - Microsoft Foundry & AI Usage
    add_screenshot_slide(
        prs,
        "Act 4: Microsoft Foundry & AI Usage Observability",
        "Page 4 (Microsoft Foundry & AI Usage) — 1:1 visual symmetry across 18 Foundry accounts, 45 AI model & tool meters, and $52.11 Jul–Sep spend",
        "Microsoft Foundry & AI Usage.png",
        [
            ("Foundry Estate Scope", "18 Accounts | 45 Meters", "224 daily meter rows across 13 resource groups and 5 Azure regions (Jul–Sep 2026)", C_BLUE),
            ("Frontier Model & Multimodal Mix", "GPT-5.4 & Sora 2", "GPT-5.4/mini ($19.01), Sora 2 & Image 2 ($14.68), GPT-4.1 ($0.94), GPT-5/5.2 ($0.33)", C_TEAL),
            ("Foundry Tools & Agent Compute", "$17.07 (32.8% Share)", "Hosted Agent vCPU ($7.29) & Memory ($1.73), SRE Agent ($1.61), AI Evaluations ($5.35)", C_RUST),
            ("Token & Compute Modality", "7 AI Modalities", "Separates Input, Output & Cached Input tokens from Hosted Agent Compute and Evaluations", C_AMBER),
        ]
    )

    # Slide 7: Page 5 - Cross-Platform Usage & Investigation
    add_screenshot_slide(
        prs,
        "Act 4: Cross-Platform Operational Investigation",
        "Page 4 (Usage & Investigation) — Strict unit isolation between Databricks DBUs/Statements and Fabric CUs/Operations",
        "Usage & Investigation.png",
        [
            ("Strict Unit Isolation", "Single-Select Platform", "Prevents invalid addition of Databricks DBUs and Fabric Capacity Units", C_TEAL),
            ("Statement & Operation Health", "4,526 DBX Queries", "247 failed statements (5.5% failure rate) vs. 4,279 finished statements", C_RUST),
            ("Attribution Coverage", "99.8% Linked DBUs", "Separates warehouse/notebook-attributed DBUs from unallocated background usage", C_BLUE),
            ("Row-Level Operational Ledger", "Daily Entity Grain", "Inspect individual warehouses, notebooks, Fabric item kinds, and outcomes", C_AMBER),
        ]
    )

    # Slide 8: Page 5 - Anomalies & Advisor Insights
    add_screenshot_slide(
        prs,
        "Act 5: Daily Cost Anomaly Detection & Azure Advisor Insights",
        "Page 5 (Anomalies & Advisor Insights) — Powered by dailyanomalybyresourcegroup (ms:DailyAnomalyByResourceGroup), Cost Alerts & Azure Advisor",
        "Anomalies & Advisor Insights.png",
        [
            ("Daily Email Alert Source", "48 Anomaly Days", "1,011 daily RG rows across 18 evaluated RGs ($3,169.33 window spend vs $3,262.67 baseline)", C_RUST),
            ("Budget & Spend Alerts", "18 High Severity", "Surfaces FDPOAzureBudget breaches ($2,711.40 / $2,375.26 vs $2,250 threshold) & spike drivers", C_AMBER),
            ("Live Azure Advisor Feed", "49 Recommendations", "12 High, 26 Medium, 11 Low across Security (36) and High Availability (13)", C_BLUE),
            ("Unified 56-Item Ledger", "$1,126.97 Impact", "Consolidates email anomaly alerts, cost drivers, budget breaches, and Advisor remediations", C_TEAL),
        ]
    )

    # Slide 9: Page 6 - Governance & Action
    add_screenshot_slide(
        prs,
        "Act 6: Bi-Directional Governance & Report-Native Tagging",
        "Page 6 (Governance & Action) — All 854 resources, manual Tag Key/Value overrides, Power Automate ARM Merge & UDF Business Context",
        "Governance & Action.png",
        [
            ("Full Resource Inventory", "All 854 Resources", "Removed 1-resource constraint; select any billed resource across 5,124 tag rows", C_TEAL),
            ("Manual Tag Key & Value Edit", "Interactive textSlicers", "Type custom Tag Key (e.g. cost-center) and Proposed Value overrides directly on page", C_BLUE),
            ("Scoped Power Automate Merge", "Least-Privilege RBAC", "Flow 619b22a5 executes ARM PATCH Merge as finops-tag-demo after explicit confirmation", C_RUST),
            ("Mutually Exclusive Modes", "Tags vs. UDF Context", "Bookmark toggle switches cleanly to AssignBusinessContext UDF for historical mapping", C_AMBER),
        ]
    )

    # Slide 10: Page 7 - Planning & Grounded AI Data Agent
    add_screenshot_slide(
        prs,
        "Act 7: Qualified Forecasting & Grounded Fabric Data Agent",
        "Page 7 (Planning) + ZavaFinOps_Agent — Rolling-origin backtest, sensitivity scenarios, and natural-language Q&A",
        "Planning.png",
        [
            ("3-Month Modeled Horizon", "$4,802.69 Baseline", "Oct–Dec 2026 trailing 3-month mean ($1,600.90/mo) with ±10% sensitivity slicer", C_BLUE),
            ("Rolling-Origin Backtest", "MAE $458.70 vs $498.69", "Evaluated on Jul–Sep 2026 holdouts; outperforms last-month naive baseline", C_TEAL),
            ("Honest Uncertainty Labels", "Illustrative ±2×MAE", "Explicitly discloses retrospective snapshot limits rather than claiming 95% CI", C_AMBER),
            ("Grounded Fabric Data Agent", "ZavaFinOps_Agent", "Grounded on all 29 tables & 62 measures with strict read-only FinOps guardrails", C_DARK_NAVY),
        ]
    )

    # Slide 9: CSA Production Implementation Roadmap
    s9 = prs.slides.add_slide(blank)
    set_slide_bg(s9, C_DARK_NAVY)
    add_header(s9, "CSA-Led Production Implementation Roadmap (12-Week Apptio Retirement Path)",
               "Structured transition from art-of-the-possible demonstration to enterprise production governance", dark=True)

    phases = [
        ("Phase 1: Weeks 1–2", "Discovery & Parity",
         "• Audit existing Apptio reports, showback/chargeback rules & tag taxonomy.\n• Lock EA/MCA subscription scope & FinOps Hub Bronze landing zone.", C_TEAL),
        ("Phase 2: Weeks 3–5", "Financial Foundation",
         "• Automate daily FOCUS 1.0r2 exports into OneLake Bronze/Silver/Gold.\n• Enable automated native-query reconciliation & quarantine gates.", C_BLUE),
        ("Phase 3: Weeks 6–8", "Platform Observability",
         "• Schedule Databricks system.* & Fabric FUAM across all workspaces.\n• Add Microsoft Foundry (Azure AI) & M365 Copilot utilization feeds.", C_RUST),
        ("Phase 4: Weeks 9–12", "Governance & Cutover",
         "• Productionize multi-subscription tag Merge flows & approval routing.\n• Enable Business Unit RLS, complete parallel sign-off & retire legacy tool.", C_AMBER),
    ]
    px = 0.5
    for p_time, p_name, p_desc, p_col in phases:
        add_card(s9, px, 1.35, 2.95, 4.2, p_time, p_name, p_desc, p_col)
        px += 3.12

    footer_box = s9.shapes.add_textbox(Inches(0.5), Inches(5.85), Inches(12.333), Inches(1.1))
    ftf = footer_box.text_frame
    ftf.word_wrap = True
    fp = ftf.paragraphs[0]
    fp.text = "Recommended Next Step: Schedule Phase 1 Architecture & Parity Workshop with your Microsoft CSA team to scope target subscriptions, Databricks workspaces, and tag governance policies."
    fp.font.name = "Segoe UI Semibold"
    fp.font.size = Pt(14)
    fp.font.color.rgb = C_WHITE

    PPTX_OUT.parent.mkdir(parents=True, exist_ok=True)
    target_pptx = PPTX_OUT
    try:
        prs.save(str(target_pptx))
    except PermissionError:
        target_pptx = PPTX_OUT.with_name("Zava-FinOps-Customer-Presentation-Governed.pptx")
        prs.save(str(target_pptx))
    return target_pptx


def set_cell_shading(cell, hex_color):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def build_docx():
    doc = docx.Document()
    for section in doc.sections:
        section.top_margin = DocxInches(0.8)
        section.bottom_margin = DocxInches(0.8)
        section.left_margin = DocxInches(0.8)
        section.right_margin = DocxInches(0.8)

    style = doc.styles["Normal"]
    style.font.name = "Segoe UI"
    style.font.size = DocxPt(10.5)
    style.font.color.rgb = DocxRGBColor(0x24, 0x24, 0x24)

    # Title block
    title_p = doc.add_paragraph()
    title_r = title_p.add_run("Zava Enterprise FinOps & Platform Observability\n")
    title_r.font.name = "Segoe UI Semibold"
    title_r.font.size = DocxPt(22)
    title_r.font.bold = True
    title_r.font.color.rgb = DocxRGBColor(0x14, 0x23, 0x3C)

    sub_r = title_p.add_run("Executive Architecture Leave-Behind, Methodology & CSA Implementation Charter")
    sub_r.font.name = "Segoe UI Semibold"
    sub_r.font.size = DocxPt(13)
    sub_r.font.color.rgb = DocxRGBColor(0x00, 0x78, 0x6B)

    meta_p = doc.add_paragraph()
    meta_p.add_run("Prepared: October 2026  |  Target Platform: Microsoft Fabric, OneLake, Direct Lake, Power Automate & Fabric Data Agent  |  Workspace: FinOps").font.size = DocxPt(9.5)

    # Section 1
    h1 = doc.add_heading("1. Executive Summary & Strategic Value", level=1)
    h1.runs[0].font.color.rgb = DocxRGBColor(0x14, 0x23, 0x3C)
    doc.add_paragraph(
        "Enterprise cloud and data leaders are increasinglyLooking to retire high-cost, third-party SaaS cost management tools (such as Apptio) in favor of a transparent, customer-owned FinOps and Platform Observability estate. "
        "While traditional cost tools ingest monthly invoice summaries into an external black box, they lack visibility into shared platform capacities (Azure Databricks DBUs, Unity Catalog assets, and Microsoft Fabric Capacity Units) and cannot execute governed remediation actions back into Azure."
    )
    bullets = [
        ("Audit-Grade Financial Reconciliation: ", "12 closed months of Azure billing (Oct 2025 – Sep 2026, 30,078 charge rows across 854 resources, $16,919.9178 exact source USD / $16,919.8317 model USD) reconciled to $0.0000 unexplained variance via an explicit 4-decimal rounding bridge (-$0.0861)."),
        ("Unified Databricks & Fabric Observability: ", "Combines live Unity Catalog inventory (262 assets across 3 catalogs and 29 schemas), Databricks billing/query system tables (504.86 DBUs, 4,526 SQL statements), and Fabric FUAM telemetry (532 active items across 39 workspaces, 1,021,021.69 Observed CUs) without conflating operational units with billed dollars."),
        ("Bi-Directional Report-Native Governance: ", "Provides all 854 billed resources with interactive Tag Key & Proposed Value editors, a report-triggered Power Automate ARM Merge flow under a least-privilege custom RBAC operator, and effective-dated internal business context via a Fabric User Data Function."),
        ("Qualified Forecasting & Grounded AI Q&A: ", "Delivers a 3-month rolling-origin backtested forecast (Oct–Dec 2026, MAE $458.70 vs. naive $498.69) and a dedicated read-only Fabric Data Agent (ZavaFinOps_Agent) grounded across all 27 tables and 53 measures."),
    ]
    for b_lead, b_text in bullets:
        p = doc.add_paragraph(style="List Bullet")
        r1 = p.add_run(b_lead)
        r1.bold = True
        p.add_run(b_text)

    # Section 2: Summary Table of Verified Demo Inventory
    h2 = doc.add_heading("2. Verified Platform & Data Source Inventory (v5 Serving Build)", level=1)
    h2.runs[0].font.color.rgb = DocxRGBColor(0x14, 0x23, 0x3C)

    table_data = [
        ("Domain", "Authoritative Source", "Verified Volume & Grain", "Key Semantic Guardrail"),
        ("Azure Financials", "Azure Cost Exports (ActualCost & FOCUS 1.0r2)", "12 months | 30,078 rows | 854 resources | $16,919.83", "Decimal(38,28) source preserved; explicit -$0.0861 rounding bridge; $0.00 unexplained."),
        ("Databricks & UC", "system.billing.usage, query.history, information_schema", "504.86 DBUs | 4,526 queries | 262 UC objects (29 schemas)", "Usage and query facts kept separate to prevent fan-out; DBUs never added to USD."),
        ("Fabric & FUAM", "FUAM_Lakehouse (capacity_metrics, active_items, workspaces)", "1,021,021.69 CUs | 47,530 ops | 532 items | 39 workspaces", "Unique day/capacity/workspace/item/operation grain; CUs isolated from DBUs & USD."),
        ("Tag Governance", "ARM Microsoft.Resources/tags & Power Automate", "854 resources | 5,124 editable tag rows | Flow 619b22a5", "Merge-only PATCH via custom RBAC operator finops-tag-demo; explicit confirmation gate."),
        ("Business Context", "Fabric SQL DB & AssignBusinessContext UDF", "64 assignable candidates | 5 controlled options | Audit trail", "Caller Entra OID validation, optimistic locking, and effective-dated historical mapping."),
        ("Planning & AI", "Forecast / Backtest tables & ZavaFinOps_Agent", "Oct–Dec 2026 ($4,802.69) | 27 tables & 53 measures", "Retrospective holdouts labeled honestly; Data Agent is strictly read-only."),
    ]
    tbl = doc.add_table(rows=len(table_data), cols=4)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    for r_idx, row_vals in enumerate(table_data):
        row = tbl.rows[r_idx]
        for c_idx, val in enumerate(row_vals):
            cell = row.cells[c_idx]
            cell.text = val
            p = cell.paragraphs[0]
            for run in p.runs:
                run.font.size = DocxPt(9.5)
                if r_idx == 0:
                    run.font.bold = True
                    run.font.color.rgb = DocxRGBColor(0xFF, 0xFF, 0xFF)
            if r_idx == 0:
                set_cell_shading(cell, "14233C")
            elif r_idx % 2 == 1:
                set_cell_shading(cell, "F3F4F5")

    # Section 3: Key Architectural Decisions
    h3 = doc.add_heading("3. Architectural Decisions: FinOps Hub, FOCUS, Security & Cost Control", level=1)
    h3.runs[0].font.color.rgb = DocxRGBColor(0x14, 0x23, 0x3C)
    decisions = [
        ("Selective FinOps Toolkit / Hub Reuse vs. Full Template Lock-In: ",
         "We evaluated FinOps Toolkit v14 directly. While FinOps Hub provides useful ADF/ADLS Gen2 export pipelines and FOCUS terminology, it is not a turnkey Lakehouse medallion or multi-platform observability layer. We adopt FOCUS 1.0r2 canonical schemas in our Silver/Gold adapters while avoiding unnecessary Hub infrastructure overhead."),
        ("Least-Privilege Action Identity Separation: ",
         "Report viewers, business callers, flow connection owners, and Azure execution identities are strictly separated. Native Azure tag writes execute via finops-tag-operator@contoso.onmicrosoft.com using custom RBAC role 'Zava FinOps Demo Tag Operator' (scoped to Microsoft.Resources/tags/read and tags/write only) with Merge-only semantics."),
        ("Cost-of-FinOps Discipline: ",
         "All serving tables, Direct Lake models, and notebooks run on an existing Fabric F4 capacity using single-executor Spark sessions (numExecutors: 1, dynamicAllocation: false) and DirectLakeOnly mode—proving enterprise FinOps does not require oversized compute."),
        ("Next-Horizon Real-Time Anomaly Detection & Operations Agent HOL Workflows: ",
         "Phase 3–4 extends the batch/daily medallion with real-time Fabric Eventstream and Eventhouse (KQL Database) feeds for sub-minute CU/DBU spike and runaway-query anomaly detection, paired with Fabric Operations Agents and Data Activator (Reflex) that route Human-on-the-Loop (HOL) Teams Adaptive Card approvals to pause idle capacities, right-size warehouses, or remediate missing tags automatically."),
    ]
    for d_lead, d_text in decisions:
        p = doc.add_paragraph(style="List Bullet")
        r1 = p.add_run(d_lead)
        r1.bold = True
        p.add_run(d_text)

    # Section 4: Visual Tour of the 7-Page Operating Experience
    h4 = doc.add_heading("4. Visual Tour of the 7-Page Operating Report", level=1)
    h4.runs[0].font.color.rgb = DocxRGBColor(0x14, 0x23, 0x3C)

    pages_info = [
        ("Page 1: Overview & Trust", "Overview & Trust.png",
         "Executive 12-month financial summary ($16,919.83), month-over-month change, unassigned share (3.4% governed / 68.4% baseline), 12/12 reconciled months, 30,078-row charge ledger, 854-resource attribution table, and one-click ZavaFinOps_Agent launch."),
        ("Page 2: Databricks & UC System Tables", "Databricks & UC System Tables.png",
         "Lakeview-style observability featuring oversized hero KPIs (29 Schemas, 259 Tables, 3 Catalogs), Tables by Format column chart, MoM table creation area chart, DBU share by product donut, daily SKU DBU burn area chart, and 262-object UC ledger."),
        ("Page 3: Fabric & FUAM System Tables", "Fabric & FUAM System Tables.png",
         "1-to-1 visual counterpart for Microsoft Fabric powered by FUAM_Lakehouse: 39 Workspaces, 532 Active Items, 5 Capacities, Active Items by Artifact Type column chart, daily operations & CU area charts, workload domain donut, and 532-item ledger."),
        ("Page 4: Microsoft Foundry & AI Usage", "Microsoft Foundry & AI Usage.png",
         "Lakeview-style observability for Microsoft Foundry Models and Foundry Tools (zava-foundry-sandbox): 18 Foundry Accounts, 45 AI Model & Tool Meters, $52.11 spend across 224 daily meter rows (GPT-5.4, Sora 2, Image 2, Hosted Agent vCPU/Memory, SRE Agent, Evaluations, and Document AI)."),
        ("Page 5: Usage & Investigation", "Usage & Investigation.png",
         "Cross-platform operational canvas with a strict single-select Platform control (Databricks vs. Fabric) to prevent mixing DBUs and CUs, paired with failure-rate tracking and a daily entity operational ledger."),
        ("Page 5: Anomalies & Advisor Insights", "Anomalies & Advisor Insights.png",
         "Surfaces the exact Azure Cost Management scheduled action data backing the daily anomaly alert email (dailyanomalybyresourcegroup / ms:DailyAnomalyByResourceGroup: 1,011 daily RG rows across 18 evaluated RGs, 48 detected anomaly days, $3,169.33 window spend vs $3,262.67 baseline), 3 Cost Management budget/forecast alerts on FDPOAzureBudget, and 49 live Azure Advisor recommendations across Security and High Availability."),
        ("Page 6: Governance & Action", "Governance & Action.png",
         "Complete 854-resource inventory, interactive Tag Key and Proposed Value textSlicers, explicit confirmation gate, bound Power Automate ARM Merge visual, bookmark toggle to AssignBusinessContext UDF, and caller/executor audit trail."),
        ("Page 7: Planning", "Planning.png",
         "Three-month modeled forecast ($4,802.69 for Oct–Dec 2026), sensitivity scenario selector (Baseline / ±10%), rolling-origin Jul–Sep holdout backtest (MAE $458.70 vs. naive $498.69), and explicit methodology/limitation disclosures."),
    ]
    for p_title, p_img, p_desc in pages_info:
        sh = doc.add_heading(p_title, level=2)
        sh.runs[0].font.color.rgb = DocxRGBColor(0x00, 0x78, 0x6B)
        doc.add_paragraph(p_desc)
        img_file = SCREENSHOTS / p_img
        if img_file.exists():
            pic_p = doc.add_paragraph()
            pic_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            pic_p.add_run().add_picture(str(img_file), width=DocxInches(6.2))

    # Section 5: CSA Production Roadmap
    h5 = doc.add_heading("5. CSA-Led Production Implementation Roadmap (12-Week Plan)", level=1)
    h5.runs[0].font.color.rgb = DocxRGBColor(0x14, 0x23, 0x3C)

    roadmap_rows = [
        ("Phase", "Timeline", "Focus", "Key Exit Criteria"),
        ("Phase 1", "Weeks 1–2", "Discovery & Parity Scoping", "Map Apptio reports, showback/chargeback rules, tag taxonomy, and subscription/EA/MCA scope."),
        ("Phase 2", "Weeks 3–5", "Automated Financial Foundation", "Automate FOCUS 1.0r2 exports into OneLake Bronze/Silver/Gold with automated native-query reconciliation."),
        ("Phase 3", "Weeks 6–8", "Platform & AI Observability", "Schedule Databricks system.* & Fabric FUAM across all workspaces; onboard Microsoft Foundry & M365 Copilot."),
        ("Phase 4", "Weeks 9–12", "Governed Action, RLS & Cutover", "Productionize multi-subscription tag Merge flows, configure Business Unit RLS, and retire legacy cost tool."),
    ]
    rtbl = doc.add_table(rows=len(roadmap_rows), cols=4)
    rtbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    for r_idx, row_vals in enumerate(roadmap_rows):
        row = rtbl.rows[r_idx]
        for c_idx, val in enumerate(row_vals):
            cell = row.cells[c_idx]
            cell.text = val
            p = cell.paragraphs[0]
            for run in p.runs:
                run.font.size = DocxPt(9.5)
                if r_idx == 0:
                    run.font.bold = True
                    run.font.color.rgb = DocxRGBColor(0xFF, 0xFF, 0xFF)
            if r_idx == 0:
                set_cell_shading(cell, "00786B")
            elif r_idx % 2 == 1:
                set_cell_shading(cell, "F3F4F5")

    DOCX_OUT.parent.mkdir(parents=True, exist_ok=True)
    target_docx = DOCX_OUT
    try:
        doc.save(str(target_docx))
    except PermissionError:
        target_docx = DOCX_OUT.with_name("Zava-FinOps-Architecture-and-CSA-Charter-Governed.docx")
        doc.save(str(target_docx))
    return target_docx


if __name__ == "__main__":
    p_out = build_pptx()
    d_out = build_docx()
    print(f"Generated PPTX: {p_out} ({p_out.stat().st_size:,} bytes)")
    print(f"Generated DOCX: {d_out} ({d_out.stat().st_size:,} bytes)")
