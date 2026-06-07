"""
PulseLinkAI - Final Project Presentation Generator
Creates an aesthetic, professional PowerPoint presentation.
"""

from pptx import Presentation
from pptx.util import Inches, Pt, Emu, Cm
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from copy import deepcopy

# ============================================================================
# COLOR PALETTE (Modern medical/tech aesthetic)
# ============================================================================
DEEP_RED = RGBColor(0xC0, 0x39, 0x2B)       # Blood red accent
DARK_BG = RGBColor(0x1A, 0x1A, 0x2E)        # Deep navy/dark
ACCENT_BLUE = RGBColor(0x16, 0x3C, 0x6E)    # Professional blue
LIGHT_ACCENT = RGBColor(0xE8, 0x4C, 0x3D)   # Vibrant red
WARM_ORANGE = RGBColor(0xF3, 0x93, 0x25)    # Warm orange
TEAL = RGBColor(0x1A, 0xBC, 0x9C)           # Teal/green
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
LIGHT_GRAY = RGBColor(0xF5, 0xF5, 0xF5)
DARK_TEXT = RGBColor(0x2C, 0x3E, 0x50)
MED_GRAY = RGBColor(0x7F, 0x8C, 0x8D)
GRADIENT_START = RGBColor(0x0F, 0x0C, 0x29)
GRADIENT_MID = RGBColor(0x30, 0x2B, 0x63)
SOFT_PURPLE = RGBColor(0x4A, 0x00, 0x82)


def set_slide_bg(slide, color):
    """Set solid background color for a slide."""
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = color


def add_shape_with_fill(slide, left, top, width, height, color, opacity=1.0, shape_type=MSO_SHAPE.RECTANGLE):
    """Add a colored rectangle shape."""
    shape = slide.shapes.add_shape(shape_type, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    return shape


def add_text_box(slide, left, top, width, height, text, font_size=18,
                 bold=False, color=WHITE, alignment=PP_ALIGN.LEFT,
                 font_name="Segoe UI"):
    """Add a text box with formatted text."""
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(font_size)
    p.font.bold = bold
    p.font.color.rgb = color
    p.font.name = font_name
    p.alignment = alignment
    return txBox


def add_bullet_points(slide, left, top, width, height, items, font_size=14,
                      color=WHITE, bullet_color=None, font_name="Segoe UI",
                      spacing=Pt(6)):
    """Add bullet points."""
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True

    for i, item in enumerate(items):
        if i == 0:
            p = tf.paragraphs[0]
        else:
            p = tf.add_paragraph()
        p.text = item
        p.font.size = Pt(font_size)
        p.font.color.rgb = color
        p.font.name = font_name
        p.space_after = spacing
        p.level = 0
    return txBox


def create_gradient_bg(slide):
    """Create a dark gradient-style background with accent shapes."""
    set_slide_bg(slide, DARK_BG)
    # Top accent bar
    bar = add_shape_with_fill(slide, Inches(0), Inches(0), Inches(10), Inches(0.06), LIGHT_ACCENT)
    return slide


def create_section_header_bg(slide):
    """Create section header background."""
    set_slide_bg(slide, ACCENT_BLUE)
    # Decorative elements
    add_shape_with_fill(slide, Inches(0), Inches(7.0), Inches(10), Inches(0.5), DEEP_RED)
    return slide


# ============================================================================
# PRESENTATION CREATION
# ============================================================================
prs = Presentation()
prs.slide_width = Inches(10)
prs.slide_height = Inches(7.5)

# Use blank layout
blank_layout = prs.slide_layouts[6]  # Blank


# ==========================================================================
# SLIDE 1: TITLE SLIDE
# ==========================================================================
slide = prs.slides.add_slide(blank_layout)
set_slide_bg(slide, DARK_BG)

# Decorative accent shapes
add_shape_with_fill(slide, Inches(0), Inches(0), Inches(10), Inches(0.08), LIGHT_ACCENT)
add_shape_with_fill(slide, Inches(0), Inches(7.35), Inches(10), Inches(0.15), DEEP_RED)

# Left decorative bar
add_shape_with_fill(slide, Inches(0.4), Inches(1.5), Inches(0.06), Inches(4.5), TEAL)

# Title
add_text_box(slide, Inches(0.8), Inches(1.8), Inches(8), Inches(1.2),
             "PulseLink", font_size=52, bold=True, color=WHITE, font_name="Segoe UI Light")

# Subtitle
add_text_box(slide, Inches(0.8), Inches(2.9), Inches(8.5), Inches(1.0),
             "The Blood Subscription for Thalassemia Care", font_size=26,
             bold=False, color=TEAL, font_name="Segoe UI")

# Description
add_text_box(slide, Inches(0.8), Inches(4.0), Inches(7.5), Inches(1.5),
             "An autonomous, AI-powered blood support network that coordinates requests,\n"
             "engages the right donors, and manages interactions in real time\nwith minimal manual effort.",
             font_size=14, color=MED_GRAY, font_name="Segoe UI")

# Event badge
badge = add_shape_with_fill(slide, Inches(0.8), Inches(5.8), Inches(3.2), Inches(0.5), DEEP_RED,
                            shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
add_text_box(slide, Inches(0.9), Inches(5.83), Inches(3.0), Inches(0.5),
             "AI for Good Hackathon 2025", font_size=13, bold=True,
             color=WHITE, alignment=PP_ALIGN.CENTER)


# ==========================================================================
# SLIDE 2: PROBLEM STATEMENT
# ==========================================================================
slide = prs.slides.add_slide(blank_layout)
create_gradient_bg(slide)

# Section label
add_text_box(slide, Inches(0.5), Inches(0.3), Inches(3), Inches(0.5),
             "THE PROBLEM", font_size=11, bold=True, color=LIGHT_ACCENT,
             font_name="Segoe UI Semibold")

# Title
add_text_box(slide, Inches(0.5), Inches(0.7), Inches(9), Inches(0.8),
             "Why Thalassemia Blood Management is Broken",
             font_size=28, bold=True, color=WHITE)

# Separator
add_shape_with_fill(slide, Inches(0.5), Inches(1.45), Inches(2.5), Inches(0.04), TEAL)

# Problem cards - Row 1
problems = [
    ("Manual Coordination", "Coordinators chase donors via\nphone calls every 2-3 weeks\nfor each patient."),
    ("No Donor Memory", "No system tracks who declined,\nwho is reliable, or who is\navailable next."),
    ("Emergency-Driven", "Each transfusion is treated as\na disconnected emergency\nrather than a recurring need."),
]

for i, (title, desc) in enumerate(problems):
    left = Inches(0.5 + i * 3.1)
    # Card background
    card = add_shape_with_fill(slide, left, Inches(1.8), Inches(2.9), Inches(2.2),
                               RGBColor(0x22, 0x22, 0x3A), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
    add_text_box(slide, left + Inches(0.2), Inches(2.0), Inches(2.5), Inches(0.5),
                 title, font_size=13, bold=True, color=WARM_ORANGE)
    add_text_box(slide, left + Inches(0.2), Inches(2.5), Inches(2.5), Inches(1.5),
                 desc, font_size=11, color=LIGHT_GRAY)

# Row 2
problems2 = [
    ("No Forecasting", "No prediction of when the\nnext transfusion is due -\nalways reactive, never proactive."),
    ("Consent & Compliance", "Donor data handling lacks\nconsent tracking and\nregulatory compliance."),
    ("Scale Challenge", "~80 patients x 4900 donors\nin one city alone - impossible\nto manage manually."),
]

for i, (title, desc) in enumerate(problems2):
    left = Inches(0.5 + i * 3.1)
    card = add_shape_with_fill(slide, left, Inches(4.3), Inches(2.9), Inches(2.2),
                               RGBColor(0x22, 0x22, 0x3A), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
    add_text_box(slide, left + Inches(0.2), Inches(4.5), Inches(2.5), Inches(0.5),
                 title, font_size=13, bold=True, color=WARM_ORANGE)
    add_text_box(slide, left + Inches(0.2), Inches(5.0), Inches(2.5), Inches(1.5),
                 desc, font_size=11, color=LIGHT_GRAY)

# Bottom stat
add_text_box(slide, Inches(0.5), Inches(6.8), Inches(9), Inches(0.5),
             "Thalassemia Major patients need blood transfusions every 14-28 days for life.",
             font_size=11, bold=True, color=DEEP_RED, alignment=PP_ALIGN.CENTER)


# ==========================================================================
# SLIDE 3: WHAT THE SYSTEM SHOULD DO (Requirements)
# ==========================================================================
slide = prs.slides.add_slide(blank_layout)
create_gradient_bg(slide)

add_text_box(slide, Inches(0.5), Inches(0.3), Inches(3), Inches(0.5),
             "REQUIREMENTS", font_size=11, bold=True, color=LIGHT_ACCENT)
add_text_box(slide, Inches(0.5), Inches(0.7), Inches(9), Inches(0.8),
             "What the System Should Be Able to Do",
             font_size=28, bold=True, color=WHITE)
add_shape_with_fill(slide, Inches(0.5), Inches(1.45), Inches(2.5), Inches(0.04), TEAL)

requirements = [
    "Handle multiple workflows through a unified intelligent AI layer",
    "Automate outreach, follow-ups, and escalations for donors",
    "Track and interpret donor responses to guide next steps",
    "React to real-time events and updates",
    "Enable conversational interactions with memory",
    "Self-manage improvement steps via failure learning",
    "Provide admins with dashboards and insights",
    "Ensure consent-aware, responsible data usage and compliant systems",
]

for i, req in enumerate(requirements):
    top = Inches(1.8 + i * 0.65)
    # Number circle
    num_shape = add_shape_with_fill(slide, Inches(0.6), top + Inches(0.05), Inches(0.35), Inches(0.35),
                                    TEAL, shape_type=MSO_SHAPE.OVAL)
    # Number text
    add_text_box(slide, Inches(0.6), top + Inches(0.05), Inches(0.35), Inches(0.35),
                 str(i+1), font_size=10, bold=True, color=WHITE, alignment=PP_ALIGN.CENTER)
    # Requirement text
    add_text_box(slide, Inches(1.2), top, Inches(8.5), Inches(0.55),
                 req, font_size=14, color=LIGHT_GRAY)


# ==========================================================================
# SLIDE 4: OUR SOLUTION - HIGH LEVEL
# ==========================================================================
slide = prs.slides.add_slide(blank_layout)
create_gradient_bg(slide)

add_text_box(slide, Inches(0.5), Inches(0.3), Inches(3), Inches(0.5),
             "OUR SOLUTION", font_size=11, bold=True, color=LIGHT_ACCENT)
add_text_box(slide, Inches(0.5), Inches(0.7), Inches(9), Inches(0.8),
             "PulseLink: Subscription-Based Blood Logistics",
             font_size=26, bold=True, color=WHITE)
add_shape_with_fill(slide, Inches(0.5), Inches(1.4), Inches(2.5), Inches(0.04), TEAL)

# Key insight box
insight_box = add_shape_with_fill(slide, Inches(0.5), Inches(1.7), Inches(9), Inches(1.0),
                                  RGBColor(0x1A, 0x3A, 0x2A), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
add_text_box(slide, Inches(0.8), Inches(1.8), Inches(8.5), Inches(0.8),
             "Core Insight: Treat recurring transfusions as a managed SUBSCRIPTION,\n"
             "not a series of disconnected emergencies. Give blood logistics a memory and a forecast.",
             font_size=13, bold=False, color=TEAL)

# 5 pillars
pillars = [
    ("Predict", "EWMA forecasting predicts\neach patient's next\ntransfusion window"),
    ("Rank", "Reliability scoring (0-100)\nranks donors by historical\nperformance"),
    ("Match", "Blood compatibility +\ngeolocation + consent\nfor optimal pairing"),
    ("Engage", "Voice IVR + WhatsApp\nauto-calls donors and\nhandles responses"),
    ("Learn", "System re-scores on\nevery response - gets\nsmarter over time"),
]

for i, (title, desc) in enumerate(pillars):
    left = Inches(0.3 + i * 1.92)
    # Pillar card
    card = add_shape_with_fill(slide, left, Inches(3.0), Inches(1.8), Inches(2.8),
                               RGBColor(0x22, 0x22, 0x3A), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
    # Accent bar on card
    add_shape_with_fill(slide, left, Inches(3.0), Inches(1.8), Inches(0.05), TEAL)
    add_text_box(slide, left + Inches(0.15), Inches(3.2), Inches(1.6), Inches(0.5),
                 title, font_size=14, bold=True, color=WARM_ORANGE)
    add_text_box(slide, left + Inches(0.15), Inches(3.7), Inches(1.6), Inches(2.0),
                 desc, font_size=10, color=LIGHT_GRAY)

# Bottom flow arrow
add_text_box(slide, Inches(0.5), Inches(6.2), Inches(9), Inches(0.8),
             "Predict Window  -->  Rank Donors  -->  Match & Assign  -->  Auto-Call  -->  Confirm/Promote",
             font_size=12, bold=True, color=MED_GRAY, alignment=PP_ALIGN.CENTER)

add_text_box(slide, Inches(0.5), Inches(6.8), Inches(9), Inches(0.5),
             "All automated. Zero manual calls needed for the happy path.",
             font_size=11, color=TEAL, alignment=PP_ALIGN.CENTER)


# ==========================================================================
# SLIDE 5: ARCHITECTURE OVERVIEW
# ==========================================================================
slide = prs.slides.add_slide(blank_layout)
create_gradient_bg(slide)

add_text_box(slide, Inches(0.5), Inches(0.3), Inches(3), Inches(0.5),
             "ARCHITECTURE", font_size=11, bold=True, color=LIGHT_ACCENT)
add_text_box(slide, Inches(0.5), Inches(0.7), Inches(9), Inches(0.8),
             "System Architecture & Data Flow",
             font_size=26, bold=True, color=WHITE)
add_shape_with_fill(slide, Inches(0.5), Inches(1.4), Inches(2.5), Inches(0.04), TEAL)

# Architecture diagram as text-based visual
arch_layers = [
    ("CLIENT LAYER", "Patient App  |  Donor Screen  |  Coordinator Dashboard\n(React 18 + Vite 5)", TEAL),
    ("API GATEWAY", "FastAPI + Uvicorn  |  RBAC  |  Pydantic v2 Validation", WARM_ORANGE),
    ("INTELLIGENCE LAYER", "Forecasting Engine  |  Matching Service  |  Reliability Scoring\n"
     "Subscription Generator  |  LLM Parsing  |  Voice Agent", LIGHT_ACCENT),
    ("MESSAGING LAYER", "Twilio Voice IVR  |  WhatsApp Business API  |  Alert Templates\n"
     "Multilingual (EN, HI, TE, TA)  |  Consent-Gated", RGBColor(0x9B, 0x59, 0xB6)),
    ("INFRASTRUCTURE", "AWS ECS Fargate  |  CloudFront CDN  |  S3  |  ALB\n"
     "Event Bus  |  Contact Encryption", MED_GRAY),
]

for i, (label, content, accent_color) in enumerate(arch_layers):
    top = Inches(1.7 + i * 1.1)
    # Layer box
    box = add_shape_with_fill(slide, Inches(0.5), top, Inches(9), Inches(0.95),
                              RGBColor(0x22, 0x22, 0x3A), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
    # Left accent
    add_shape_with_fill(slide, Inches(0.5), top, Inches(0.06), Inches(0.95), accent_color)
    # Label
    add_text_box(slide, Inches(0.75), top + Inches(0.05), Inches(2.5), Inches(0.3),
                 label, font_size=9, bold=True, color=accent_color)
    # Content
    add_text_box(slide, Inches(0.75), top + Inches(0.35), Inches(8.5), Inches(0.6),
                 content, font_size=10, color=LIGHT_GRAY)


# ==========================================================================
# SLIDE 6: FIVE CORE AI SERVICES
# ==========================================================================
slide = prs.slides.add_slide(blank_layout)
create_gradient_bg(slide)

add_text_box(slide, Inches(0.5), Inches(0.3), Inches(3), Inches(0.5),
             "CORE SERVICES", font_size=11, bold=True, color=LIGHT_ACCENT)
add_text_box(slide, Inches(0.5), Inches(0.7), Inches(9), Inches(0.8),
             "Five Intelligent Microservices",
             font_size=26, bold=True, color=WHITE)
add_shape_with_fill(slide, Inches(0.5), Inches(1.4), Inches(2.5), Inches(0.04), TEAL)

services = [
    ("1. LLM Parsing Service", "Converts messy WhatsApp text into structured JSON with confidence scores.\n"
     "Supports multilingual input (EN/HI/TE). Schema-validated extraction with retry."),
    ("2. Forecasting Engine", "EWMA cadence estimation predicts next transfusion window [start, expected, end].\n"
     "Re-learns after each transfusion. Confidence grows with history."),
    ("3. Donor Reliability Scoring", "0-100 score from 4 weighted signals: Acceptance Ratio (40%), Call Efficiency (20%),\n"
     "Volume Factor (20%), Recency Factor (20%). Tiers: Anchor / Steady / Growing."),
    ("4. Subscription Generator", "Builds rolling recurring slot plans. Matches donors by compatibility + reliability.\n"
     "Auto-promotes backup on decline. Generates future-dated windows."),
    ("5. Messaging & Voice Service", "Multilingual WhatsApp alerts + Twilio IVR voice calls. 4 alert types.\n"
     "Auto-calls next donor on decline. Consent-gated communication."),
]

for i, (title, desc) in enumerate(services):
    top = Inches(1.7 + i * 1.1)
    box = add_shape_with_fill(slide, Inches(0.5), top, Inches(9), Inches(1.0),
                              RGBColor(0x22, 0x22, 0x3A), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
    add_shape_with_fill(slide, Inches(0.5), top, Inches(0.06), Inches(1.0), TEAL)
    add_text_box(slide, Inches(0.8), top + Inches(0.08), Inches(8.5), Inches(0.35),
                 title, font_size=13, bold=True, color=WARM_ORANGE)
    add_text_box(slide, Inches(0.8), top + Inches(0.4), Inches(8.5), Inches(0.6),
                 desc, font_size=10, color=LIGHT_GRAY)


# ==========================================================================
# SLIDE 7: VOICE AGENT (IVR CALL FLOW)
# ==========================================================================
slide = prs.slides.add_slide(blank_layout)
create_gradient_bg(slide)

add_text_box(slide, Inches(0.5), Inches(0.3), Inches(3), Inches(0.5),
             "VOICE AGENT", font_size=11, bold=True, color=LIGHT_ACCENT)
add_text_box(slide, Inches(0.5), Inches(0.7), Inches(9), Inches(0.8),
             "Automated IVR Donor Call Flow",
             font_size=26, bold=True, color=WHITE)
add_shape_with_fill(slide, Inches(0.5), Inches(1.4), Inches(2.5), Inches(0.04), TEAL)

# Flow steps
steps = [
    ("1", "Coordinator clicks 'Start Call' for a patient", TEAL),
    ("2", "Twilio places outbound call to top-ranked donor", WARM_ORANGE),
    ("3", "Donor hears language menu: 1=English, 2=Hindi, 3=Telugu", TEAL),
    ("4", "Donor hears slot offer in their language - Press 1 to Accept, 2 to Decline", WARM_ORANGE),
    ("5", "On ACCEPT: Slot confirmed, WhatsApp sent to donor + patient", TEAL),
    ("6", "On DECLINE: Reliability re-scored, next backup auto-promoted & called", LIGHT_ACCENT),
    ("7", "If ALL decline: Coordinator receives WhatsApp alert to intervene", DEEP_RED),
]

for i, (num, text, color) in enumerate(steps):
    top = Inches(1.7 + i * 0.75)
    # Step number
    circle = add_shape_with_fill(slide, Inches(0.7), top + Inches(0.08), Inches(0.35), Inches(0.35),
                                 color, shape_type=MSO_SHAPE.OVAL)
    add_text_box(slide, Inches(0.7), top + Inches(0.08), Inches(0.35), Inches(0.35),
                 num, font_size=10, bold=True, color=WHITE, alignment=PP_ALIGN.CENTER)
    # Step text
    add_text_box(slide, Inches(1.3), top + Inches(0.05), Inches(8), Inches(0.5),
                 text, font_size=13, color=LIGHT_GRAY)
    # Connector line
    if i < len(steps) - 1:
        add_shape_with_fill(slide, Inches(0.87), top + Inches(0.45), Inches(0.02), Inches(0.35),
                            RGBColor(0x44, 0x44, 0x55))

# Key insight box
add_shape_with_fill(slide, Inches(0.5), Inches(7.0), Inches(9), Inches(0.4),
                    RGBColor(0x1A, 0x3A, 0x2A), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
add_text_box(slide, Inches(0.8), Inches(7.05), Inches(8.5), Inches(0.35),
             "Zero manual intervention in the happy path. Fully automated donor engagement.",
             font_size=11, bold=True, color=TEAL, alignment=PP_ALIGN.CENTER)


# ==========================================================================
# SLIDE 8: RELIABILITY SCORING (IMPLEMENTATION)
# ==========================================================================
slide = prs.slides.add_slide(blank_layout)
create_gradient_bg(slide)

add_text_box(slide, Inches(0.5), Inches(0.3), Inches(3), Inches(0.5),
             "IMPLEMENTATION", font_size=11, bold=True, color=LIGHT_ACCENT)
add_text_box(slide, Inches(0.5), Inches(0.7), Inches(9), Inches(0.8),
             "Donor Reliability Scoring Algorithm",
             font_size=26, bold=True, color=WHITE)
add_shape_with_fill(slide, Inches(0.5), Inches(1.4), Inches(2.5), Inches(0.04), TEAL)

# Formula box
formula_box = add_shape_with_fill(slide, Inches(0.5), Inches(1.7), Inches(9), Inches(1.2),
                                  RGBColor(0x15, 0x15, 0x28), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
add_text_box(slide, Inches(0.8), Inches(1.8), Inches(8.5), Inches(0.4),
             "Score = 0.40 x AcceptanceRatio + 0.20 x CallEfficiency + 0.20 x VolumeFactor + 0.20 x RecencyFactor",
             font_size=12, bold=True, color=WARM_ORANGE, alignment=PP_ALIGN.CENTER)
add_text_box(slide, Inches(0.8), Inches(2.3), Inches(8.5), Inches(0.5),
             "Each factor in [0,1] | Weighted sum x 100 = Score in [0, 100] | Clamped defensively",
             font_size=10, color=MED_GRAY, alignment=PP_ALIGN.CENTER)

# Factor details
factors = [
    ("Acceptance Ratio (40%)", "accepted / offered\nNeutral prior of 0.5 for new donors", TEAL),
    ("Call Efficiency (20%)", "1 / max(1, calls_to_donations_ratio)\nFewer calls per donation = higher score", WARM_ORANGE),
    ("Volume Factor (20%)", "min(1, donations / 10)\nRewards repeat donors, caps at 10", TEAL),
    ("Recency Factor (20%)", "0.5 ^ (days_since / 120)\nExponential decay, 120-day half-life", WARM_ORANGE),
]

for i, (title, desc, color) in enumerate(factors):
    left = Inches(0.5 + (i % 2) * 4.7)
    top = Inches(3.2 + (i // 2) * 1.6)
    card = add_shape_with_fill(slide, left, top, Inches(4.4), Inches(1.4),
                               RGBColor(0x22, 0x22, 0x3A), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
    add_shape_with_fill(slide, left, top, Inches(4.4), Inches(0.05), color)
    add_text_box(slide, left + Inches(0.2), top + Inches(0.15), Inches(4.0), Inches(0.4),
                 title, font_size=12, bold=True, color=color)
    add_text_box(slide, left + Inches(0.2), top + Inches(0.55), Inches(4.0), Inches(0.8),
                 desc, font_size=10, color=LIGHT_GRAY)

# Tier assignment
add_text_box(slide, Inches(0.5), Inches(6.5), Inches(9), Inches(0.4),
             "Tier Assignment:  Anchor (score >= 75 & acceptance >= 70%)  |  Steady (score >= 45)  |  Growing (< 45)",
             font_size=11, bold=True, color=MED_GRAY, alignment=PP_ALIGN.CENTER)
add_text_box(slide, Inches(0.5), Inches(6.9), Inches(9), Inches(0.4),
             "Guarantee: A DECLINED response can NEVER raise the donor's score (monotonic decrease)",
             font_size=10, bold=True, color=DEEP_RED, alignment=PP_ALIGN.CENTER)


# ==========================================================================
# SLIDE 9: DECLINE -> PROMOTE -> RE-SCORE FLOW
# ==========================================================================
slide = prs.slides.add_slide(blank_layout)
create_gradient_bg(slide)

add_text_box(slide, Inches(0.5), Inches(0.3), Inches(3), Inches(0.5),
             "SELF-HEALING FLOW", font_size=11, bold=True, color=LIGHT_ACCENT)
add_text_box(slide, Inches(0.5), Inches(0.7), Inches(9), Inches(0.8),
             "Decline --> Promote --> Re-Score",
             font_size=26, bold=True, color=WHITE)
add_shape_with_fill(slide, Inches(0.5), Inches(1.4), Inches(2.5), Inches(0.04), TEAL)

# Description
add_text_box(slide, Inches(0.5), Inches(1.7), Inches(9), Inches(0.6),
             "When a donor declines, the system autonomously handles the entire cascade:",
             font_size=13, color=LIGHT_GRAY)

flow_steps = [
    ("Mark Declined", "Assignment status set to DECLINED. Idempotent - duplicate events are no-ops."),
    ("Re-Score Donor", "Event published to bus. ReliabilityRescorer recomputes score (guaranteed <= prior)."),
    ("Auto-Promote Backup", "Highest-ranked eligible backup promoted to primary (rank 0). Ranks swapped."),
    ("Notify Promoted Donor", "Localized WhatsApp offer sent to newly promoted donor."),
    ("Escalate if Needed", "If no eligible backup exists, coordinator receives alert to intervene."),
]

for i, (title, desc) in enumerate(flow_steps):
    top = Inches(2.4 + i * 0.95)
    # Step indicator
    circle = add_shape_with_fill(slide, Inches(0.6), top + Inches(0.1), Inches(0.4), Inches(0.4),
                                 LIGHT_ACCENT if i < 3 else WARM_ORANGE if i == 3 else DEEP_RED,
                                 shape_type=MSO_SHAPE.OVAL)
    add_text_box(slide, Inches(0.6), top + Inches(0.1), Inches(0.4), Inches(0.4),
                 str(i+1), font_size=10, bold=True, color=WHITE, alignment=PP_ALIGN.CENTER)
    # Content
    add_text_box(slide, Inches(1.3), top, Inches(3.5), Inches(0.4),
                 title, font_size=13, bold=True, color=WARM_ORANGE)
    add_text_box(slide, Inches(1.3), top + Inches(0.4), Inches(8), Inches(0.5),
                 desc, font_size=10, color=LIGHT_GRAY)
    # Connector
    if i < len(flow_steps) - 1:
        add_shape_with_fill(slide, Inches(0.79), top + Inches(0.52), Inches(0.02), Inches(0.45),
                            RGBColor(0x44, 0x44, 0x55))

# Key properties box
add_shape_with_fill(slide, Inches(0.5), Inches(7.0), Inches(9), Inches(0.4),
                    RGBColor(0x1A, 0x2A, 0x3A), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
add_text_box(slide, Inches(0.7), Inches(7.05), Inches(8.5), Inches(0.35),
             "Properties: Idempotent | Event-driven | Exactly-once publish | Monotonic score decrease",
             font_size=10, bold=True, color=TEAL, alignment=PP_ALIGN.CENTER)


# ==========================================================================
# SLIDE 10: TECH STACK
# ==========================================================================
slide = prs.slides.add_slide(blank_layout)
create_gradient_bg(slide)

add_text_box(slide, Inches(0.5), Inches(0.3), Inches(3), Inches(0.5),
             "TECH STACK", font_size=11, bold=True, color=LIGHT_ACCENT)
add_text_box(slide, Inches(0.5), Inches(0.7), Inches(9), Inches(0.8),
             "Technology Choices & Justification",
             font_size=26, bold=True, color=WHITE)
add_shape_with_fill(slide, Inches(0.5), Inches(1.4), Inches(2.5), Inches(0.04), TEAL)

# Backend column
add_text_box(slide, Inches(0.5), Inches(1.7), Inches(4), Inches(0.4),
             "BACKEND", font_size=12, bold=True, color=TEAL)

backend_stack = [
    "Python 3.11 + FastAPI",
    "Pydantic v2 (validation)",
    "scikit-learn (ML scoring)",
    "Twilio Voice + WhatsApp API",
    "SQLAlchemy 2.0 + PostgreSQL",
    "EWMA Forecasting (custom)",
    "Event-driven architecture",
]
add_bullet_points(slide, Inches(0.5), Inches(2.1), Inches(4.2), Inches(3.5),
                  backend_stack, font_size=11, color=LIGHT_GRAY)

# Frontend column
add_text_box(slide, Inches(5.2), Inches(1.7), Inches(4), Inches(0.4),
             "FRONTEND", font_size=12, bold=True, color=WARM_ORANGE)

frontend_stack = [
    "React 18 + Vite 5",
    "3 standalone apps (Patient/Donor/Coord)",
    "Real-time polling (2s intervals)",
    "Responsive vanilla CSS",
    "Install-free donor screen",
    "Multilingual UI support",
    "Risk-ranked dashboard tables",
]
add_bullet_points(slide, Inches(5.2), Inches(2.1), Inches(4.5), Inches(3.5),
                  frontend_stack, font_size=11, color=LIGHT_GRAY)

# Infrastructure section
add_shape_with_fill(slide, Inches(0.5), Inches(5.2), Inches(9), Inches(0.04), RGBColor(0x33, 0x33, 0x44))
add_text_box(slide, Inches(0.5), Inches(5.4), Inches(4), Inches(0.4),
             "INFRASTRUCTURE (AWS)", font_size=12, bold=True, color=RGBColor(0x9B, 0x59, 0xB6))

infra_items = [
    "ECS Fargate (serverless containers)",
    "CloudFront CDN (4 distributions)",
    "S3 Static Hosting (3 frontend apps)",
    "Application Load Balancer",
    "Amazon ECR (container registry)",
    "~$35/month estimated cost",
]
add_bullet_points(slide, Inches(0.5), Inches(5.8), Inches(9), Inches(1.8),
                  infra_items, font_size=11, color=LIGHT_GRAY)


# ==========================================================================
# SLIDE 11: WHATSAPP + MULTILINGUAL ALERTS
# ==========================================================================
slide = prs.slides.add_slide(blank_layout)
create_gradient_bg(slide)

add_text_box(slide, Inches(0.5), Inches(0.3), Inches(3), Inches(0.5),
             "MESSAGING", font_size=11, bold=True, color=LIGHT_ACCENT)
add_text_box(slide, Inches(0.5), Inches(0.7), Inches(9), Inches(0.8),
             "WhatsApp Alerts & Multilingual Support",
             font_size=26, bold=True, color=WHITE)
add_shape_with_fill(slide, Inches(0.5), Inches(1.4), Inches(2.5), Inches(0.04), TEAL)

# Alert types
alert_types = [
    ("UPCOMING_REMINDER", "Sent to primary donor when their window is within N days.\n"
     "Proactively reduces manual call volume.", TEAL),
    ("DONOR_ACCEPTED", "Confirmation to donor after accepting via IVR.\n"
     "Reinforces positive behavior and builds trust.", WARM_ORANGE),
    ("PATIENT_ARRANGED", "'Blood is arranged' reassurance to patient/family.\n"
     "Reduces anxiety for recurring transfusion patients.", RGBColor(0x9B, 0x59, 0xB6)),
    ("DONOR_DECLINED", "Polite acknowledgement to declining donor.\n"
     "Maintains long-term donor relationship & engagement.", LIGHT_ACCENT),
]

for i, (title, desc, color) in enumerate(alert_types):
    top = Inches(1.7 + i * 1.3)
    card = add_shape_with_fill(slide, Inches(0.5), top, Inches(5.5), Inches(1.15),
                               RGBColor(0x22, 0x22, 0x3A), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
    add_shape_with_fill(slide, Inches(0.5), top, Inches(0.06), Inches(1.15), color)
    add_text_box(slide, Inches(0.8), top + Inches(0.1), Inches(5.0), Inches(0.35),
                 title, font_size=12, bold=True, color=color)
    add_text_box(slide, Inches(0.8), top + Inches(0.45), Inches(5.0), Inches(0.7),
                 desc, font_size=10, color=LIGHT_GRAY)

# Languages box
lang_box = add_shape_with_fill(slide, Inches(6.3), Inches(1.7), Inches(3.2), Inches(5.5),
                               RGBColor(0x22, 0x22, 0x3A), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
add_text_box(slide, Inches(6.5), Inches(1.9), Inches(3.0), Inches(0.4),
             "LANGUAGES", font_size=11, bold=True, color=TEAL)

languages = [
    "English (en)",
    "Hindi (hi)",
    "Telugu (te)",
    "Tamil (ta)",
]
for i, lang in enumerate(languages):
    add_text_box(slide, Inches(6.5), Inches(2.4 + i * 0.5), Inches(3.0), Inches(0.4),
                 lang, font_size=12, color=LIGHT_GRAY)

add_text_box(slide, Inches(6.5), Inches(4.5), Inches(3.0), Inches(0.4),
             "CHANNELS", font_size=11, bold=True, color=WARM_ORANGE)
channels = ["WhatsApp Business API", "Voice IVR (DTMF)", "SMS Fallback"]
for i, ch in enumerate(channels):
    add_text_box(slide, Inches(6.5), Inches(5.0 + i * 0.45), Inches(3.0), Inches(0.4),
                 ch, font_size=11, color=LIGHT_GRAY)

add_text_box(slide, Inches(6.5), Inches(6.3), Inches(3.0), Inches(0.4),
             "CONSENT-GATED", font_size=11, bold=True, color=DEEP_RED)
add_text_box(slide, Inches(6.5), Inches(6.7), Inches(2.8), Inches(0.5),
             "All messages require\nactive consent scope", font_size=10, color=LIGHT_GRAY)


# ==========================================================================
# SLIDE 12: COORDINATOR DASHBOARD
# ==========================================================================
slide = prs.slides.add_slide(blank_layout)
create_gradient_bg(slide)

add_text_box(slide, Inches(0.5), Inches(0.3), Inches(3), Inches(0.5),
             "DASHBOARD", font_size=11, bold=True, color=LIGHT_ACCENT)
add_text_box(slide, Inches(0.5), Inches(0.7), Inches(9), Inches(0.8),
             "Coordinator Dashboard - 4 Tabs",
             font_size=26, bold=True, color=WHITE)
add_shape_with_fill(slide, Inches(0.5), Inches(1.4), Inches(2.5), Inches(0.04), TEAL)

tabs = [
    ("Patients Tab", "Risk-ranked patient table (urgency x low coverage x low confidence).\n"
     "Each row has 'Start Call' and 'View' buttons. Expands inline donor panel\n"
     "showing 8 assigned donors with blood match %, reliability score, and status.",
     TEAL),
    ("Call Flow Tab", "Live IVR session status with 2-second polling. Shows donor queue,\n"
     "current donor being called, language selected, accept/decline outcome.\n"
     "Has a 'Simulate' panel for testing without a real phone.",
     WARM_ORANGE),
    ("WhatsApp Log Tab", "All sent alerts with recipient, phone, message text, timestamp.\n"
     "'Send Reminders' button triggers proactive outreach to donors whose\n"
     "windows are approaching within N days.",
     RGBColor(0x9B, 0x59, 0xB6)),
    ("Inbox Tab", "Incoming WhatsApp messages from new patients. LLM auto-parses them.\n"
     "Coordinator reviews parsed fields with confidence indicators and clicks\n"
     "'Add Patient to System' to onboard.",
     LIGHT_ACCENT),
]

for i, (title, desc, color) in enumerate(tabs):
    top = Inches(1.7 + i * 1.4)
    card = add_shape_with_fill(slide, Inches(0.5), top, Inches(9), Inches(1.25),
                               RGBColor(0x22, 0x22, 0x3A), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
    add_shape_with_fill(slide, Inches(0.5), top, Inches(9), Inches(0.05), color)
    add_text_box(slide, Inches(0.8), top + Inches(0.1), Inches(8.5), Inches(0.35),
                 title, font_size=13, bold=True, color=color)
    add_text_box(slide, Inches(0.8), top + Inches(0.45), Inches(8.5), Inches(0.85),
                 desc, font_size=10, color=LIGHT_GRAY)


# ==========================================================================
# SLIDE 13: LLM PARSING - IMPLEMENTATION
# ==========================================================================
slide = prs.slides.add_slide(blank_layout)
create_gradient_bg(slide)

add_text_box(slide, Inches(0.5), Inches(0.3), Inches(3), Inches(0.5),
             "AI PARSING", font_size=11, bold=True, color=LIGHT_ACCENT)
add_text_box(slide, Inches(0.5), Inches(0.7), Inches(9), Inches(0.8),
             "LLM-Powered Message Parsing",
             font_size=26, bold=True, color=WHITE)
add_shape_with_fill(slide, Inches(0.5), Inches(1.4), Inches(2.5), Inches(0.04), TEAL)

# Input example
add_text_box(slide, Inches(0.5), Inches(1.7), Inches(4), Inches(0.4),
             "INPUT (Messy WhatsApp Text):", font_size=11, bold=True, color=WARM_ORANGE)
input_box = add_shape_with_fill(slide, Inches(0.5), Inches(2.1), Inches(4.3), Inches(1.2),
                                RGBColor(0x15, 0x15, 0x28), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
add_text_box(slide, Inches(0.7), Inches(2.2), Inches(4.0), Inches(1.0),
             '"Ravi B- every 18 days last\ntransfusion 24th Jan 2025\n3 units donor ph 9876543210"',
             font_size=11, color=RGBColor(0x82, 0xE0, 0xAA))

# Output example
add_text_box(slide, Inches(5.2), Inches(1.7), Inches(4.5), Inches(0.4),
             "OUTPUT (Structured JSON):", font_size=11, bold=True, color=TEAL)
output_box = add_shape_with_fill(slide, Inches(5.2), Inches(2.1), Inches(4.3), Inches(1.2),
                                 RGBColor(0x15, 0x15, 0x28), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
add_text_box(slide, Inches(5.4), Inches(2.2), Inches(4.0), Inches(1.0),
             '{ blood_group: "B Negative",\n  cadence_days: 18,\n  last_transfusion: "2025-01-24",\n  units: 3, confidence: 0.95 }',
             font_size=10, color=RGBColor(0x82, 0xE0, 0xAA))

# Pipeline
add_text_box(slide, Inches(0.5), Inches(3.6), Inches(9), Inches(0.4),
             "PARSING PIPELINE", font_size=12, bold=True, color=WHITE)

pipeline_steps = [
    ("Structured LLM Call", "Schema-validated extraction\ntemp=0, deterministic"),
    ("Defensive Validation", "Re-validate against schema\nNever trust raw output"),
    ("Normalization", "Dates to ISO 8601\nBlood groups canonical"),
    ("Review Flags", "Low confidence < 0.75\nNull fields flagged"),
    ("Retry on Failure", "One retry with stricter\nprompt, then graceful fail"),
]

for i, (title, desc) in enumerate(pipeline_steps):
    left = Inches(0.3 + i * 1.92)
    card = add_shape_with_fill(slide, left, Inches(4.1), Inches(1.8), Inches(1.6),
                               RGBColor(0x22, 0x22, 0x3A), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
    add_shape_with_fill(slide, left, Inches(4.1), Inches(1.8), Inches(0.04), TEAL)
    add_text_box(slide, left + Inches(0.1), Inches(4.25), Inches(1.6), Inches(0.4),
                 title, font_size=9, bold=True, color=WARM_ORANGE)
    add_text_box(slide, left + Inches(0.1), Inches(4.6), Inches(1.6), Inches(1.0),
                 desc, font_size=9, color=LIGHT_GRAY)

# Provider info
add_text_box(slide, Inches(0.5), Inches(6.0), Inches(9), Inches(0.8),
             "Providers: MockLlmClient (offline, regex-based) | BedrockClaudeClient (Amazon Bedrock, Claude Haiku)\n"
             "Supports: English, Hindi, Telugu mixed input | ISO dates, natural language dates, relative dates",
             font_size=10, color=MED_GRAY, alignment=PP_ALIGN.CENTER)

# Privacy guarantee
add_shape_with_fill(slide, Inches(0.5), Inches(6.9), Inches(9), Inches(0.4),
                    RGBColor(0x1A, 0x2A, 0x3A), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
add_text_box(slide, Inches(0.7), Inches(6.95), Inches(8.5), Inches(0.35),
             "Privacy: Parser performs ZERO persistence. Data only saved after coordinator review & consent.",
             font_size=10, bold=True, color=TEAL, alignment=PP_ALIGN.CENTER)


# ==========================================================================
# SLIDE 14: CONSENT & COMPLIANCE
# ==========================================================================
slide = prs.slides.add_slide(blank_layout)
create_gradient_bg(slide)

add_text_box(slide, Inches(0.5), Inches(0.3), Inches(3), Inches(0.5),
             "COMPLIANCE", font_size=11, bold=True, color=LIGHT_ACCENT)
add_text_box(slide, Inches(0.5), Inches(0.7), Inches(9), Inches(0.8),
             "Consent-Aware & Responsible Data Usage",
             font_size=26, bold=True, color=WHITE)
add_shape_with_fill(slide, Inches(0.5), Inches(1.4), Inches(2.5), Inches(0.04), TEAL)

compliance_features = [
    ("Contact Encryption", "All donor contact points encrypted with symmetric key.\n"
     "Plain text never stored in database or logs."),
    ("Consent Store", "Active consent scopes tracked per donor.\n"
     "'contact_for_slots' scope required before ANY outreach."),
    ("Consent Gates", "Every message channel (Voice, WhatsApp, SMS)\n"
     "checks consent BEFORE sending. No consent = no contact."),
    ("Audit Trail", "Every action logged with timestamp, actor, and context.\n"
     "Full traceability for regulatory compliance."),
    ("No Persistence in Parsing", "LLM parser never writes to DB.\n"
     "Data saved only after human coordinator review."),
    ("RBAC (Role-Based Access)", "Coordinators, patients, and donors see\n"
     "only what they're authorized to access."),
]

for i, (title, desc) in enumerate(compliance_features):
    left = Inches(0.5 + (i % 2) * 4.7)
    top = Inches(1.7 + (i // 2) * 1.7)
    card = add_shape_with_fill(slide, left, top, Inches(4.4), Inches(1.5),
                               RGBColor(0x22, 0x22, 0x3A), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
    add_shape_with_fill(slide, left, top, Inches(0.06), Inches(1.5), TEAL)
    add_text_box(slide, left + Inches(0.25), top + Inches(0.1), Inches(4.0), Inches(0.4),
                 title, font_size=12, bold=True, color=WARM_ORANGE)
    add_text_box(slide, left + Inches(0.25), top + Inches(0.5), Inches(4.0), Inches(1.0),
                 desc, font_size=10, color=LIGHT_GRAY)


# ==========================================================================
# SLIDE 15: DEPLOYMENT & LIVE DEMO
# ==========================================================================
slide = prs.slides.add_slide(blank_layout)
create_gradient_bg(slide)

add_text_box(slide, Inches(0.5), Inches(0.3), Inches(3), Inches(0.5),
             "DEPLOYMENT", font_size=11, bold=True, color=LIGHT_ACCENT)
add_text_box(slide, Inches(0.5), Inches(0.7), Inches(9), Inches(0.8),
             "Live on AWS - Production Deployment",
             font_size=26, bold=True, color=WHITE)
add_shape_with_fill(slide, Inches(0.5), Inches(1.4), Inches(2.5), Inches(0.04), TEAL)

# Live URLs
urls = [
    ("Coordinator Dashboard", "dpijq2esptlq8.cloudfront.net"),
    ("Patient App", "d1c51x56ezsfgz.cloudfront.net"),
    ("Donor Screen", "d1ism9anjs6w7j.cloudfront.net"),
    ("Backend API / Swagger", "d1u7u2ctmq6ryx.cloudfront.net/docs"),
]

for i, (label, url) in enumerate(urls):
    top = Inches(1.7 + i * 0.7)
    card = add_shape_with_fill(slide, Inches(0.5), top, Inches(9), Inches(0.6),
                               RGBColor(0x22, 0x22, 0x3A), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
    add_text_box(slide, Inches(0.8), top + Inches(0.12), Inches(3.0), Inches(0.4),
                 label, font_size=12, bold=True, color=WARM_ORANGE)
    add_text_box(slide, Inches(4.0), top + Inches(0.12), Inches(5.5), Inches(0.4),
                 f"https://{url}", font_size=11, color=TEAL)

# AWS Architecture summary
add_text_box(slide, Inches(0.5), Inches(4.7), Inches(9), Inches(0.4),
             "AWS PRODUCTION ARCHITECTURE", font_size=12, bold=True, color=WHITE)

arch_text = (
    "Browser --> CloudFront (HTTPS) --> ALB (HTTP:80) --> ECS Fargate Task\n"
    "                                                     (FastAPI + in-memory state)\n"
    "3 x S3 Buckets --> 3 x CloudFront Distributions (Frontend Apps)\n"
    "Twilio (Voice IVR + WhatsApp) --> Webhook endpoints on Fargate\n"
    "Dataset.csv baked into Docker image | 512 CPU / 1 GB RAM"
)
arch_box = add_shape_with_fill(slide, Inches(0.5), Inches(5.1), Inches(9), Inches(1.8),
                               RGBColor(0x15, 0x15, 0x28), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
add_text_box(slide, Inches(0.7), Inches(5.2), Inches(8.5), Inches(1.7),
             arch_text, font_size=10, color=RGBColor(0x82, 0xE0, 0xAA))

# Cost
add_text_box(slide, Inches(0.5), Inches(7.0), Inches(9), Inches(0.4),
             "Estimated monthly cost: ~$35  |  Deployment: 4 PowerShell scripts (push, infra, frontends, HTTPS)",
             font_size=10, bold=True, color=MED_GRAY, alignment=PP_ALIGN.CENTER)


# ==========================================================================
# SLIDE 16: HOW IT ADDRESSES THE PROBLEM
# ==========================================================================
slide = prs.slides.add_slide(blank_layout)
create_gradient_bg(slide)

add_text_box(slide, Inches(0.5), Inches(0.3), Inches(3), Inches(0.5),
             "IMPACT", font_size=11, bold=True, color=LIGHT_ACCENT)
add_text_box(slide, Inches(0.5), Inches(0.7), Inches(9), Inches(0.8),
             "How PulseLink Solves Each Requirement",
             font_size=26, bold=True, color=WHITE)
add_shape_with_fill(slide, Inches(0.5), Inches(1.4), Inches(2.5), Inches(0.04), TEAL)

mappings = [
    ("Unified AI layer", "5 microservices (Parsing, Forecasting, Scoring, Matching, Subscription) under one API"),
    ("Automate outreach", "Voice IVR auto-calls donors; WhatsApp reminders reduce manual calls by design"),
    ("Track responses", "Every accept/decline logged, scored, and used to improve future rankings"),
    ("Real-time events", "Event bus publishes donor responses; 2-second polling for live status"),
    ("Conversational memory", "LLM parses inbound WhatsApp; system remembers all donor history"),
    ("Self-improvement", "Reliability score re-learns on every response; EWMA adapts cadence"),
    ("Admin dashboards", "4-tab coordinator dashboard with risk ranking, call flow, alerts, inbox"),
    ("Consent compliance", "Consent store + encryption + gates on every channel. RBAC. No-persist parsing"),
]

for i, (req, solution) in enumerate(mappings):
    top = Inches(1.6 + i * 0.7)
    add_text_box(slide, Inches(0.6), top + Inches(0.05), Inches(2.8), Inches(0.5),
                 req, font_size=11, bold=True, color=WARM_ORANGE)
    add_text_box(slide, Inches(3.5), top + Inches(0.05), Inches(6.2), Inches(0.5),
                 solution, font_size=10, color=LIGHT_GRAY)
    if i < len(mappings) - 1:
        add_shape_with_fill(slide, Inches(0.6), top + Inches(0.55), Inches(8.8), Inches(0.01),
                            RGBColor(0x33, 0x33, 0x44))


# ==========================================================================
# SLIDE 17: KEY METRICS & DATASET
# ==========================================================================
slide = prs.slides.add_slide(blank_layout)
create_gradient_bg(slide)

add_text_box(slide, Inches(0.5), Inches(0.3), Inches(3), Inches(0.5),
             "DATA & SCALE", font_size=11, bold=True, color=LIGHT_ACCENT)
add_text_box(slide, Inches(0.5), Inches(0.7), Inches(9), Inches(0.8),
             "Dataset & Key Numbers",
             font_size=26, bold=True, color=WHITE)
add_shape_with_fill(slide, Inches(0.5), Inches(1.4), Inches(2.5), Inches(0.04), TEAL)

# Big numbers
metrics = [
    ("~80", "Patients", "Thalassemia Major\nHyderabad region"),
    ("~4,900", "Donors", "Registered blood\ndonors in network"),
    ("8", "Donors/Slot", "Primary + 7 backups\nper transfusion slot"),
    ("4", "Languages", "EN, HI, TE, TA\nFull localization"),
]

for i, (number, label, desc) in enumerate(metrics):
    left = Inches(0.5 + i * 2.35)
    card = add_shape_with_fill(slide, left, Inches(1.7), Inches(2.15), Inches(2.5),
                               RGBColor(0x22, 0x22, 0x3A), shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
    add_text_box(slide, left + Inches(0.1), Inches(1.9), Inches(2.0), Inches(0.8),
                 number, font_size=28, bold=True, color=TEAL, alignment=PP_ALIGN.CENTER)
    add_text_box(slide, left + Inches(0.1), Inches(2.7), Inches(2.0), Inches(0.4),
                 label, font_size=12, bold=True, color=WARM_ORANGE, alignment=PP_ALIGN.CENTER)
    add_text_box(slide, left + Inches(0.1), Inches(3.2), Inches(2.0), Inches(0.7),
                 desc, font_size=10, color=LIGHT_GRAY, alignment=PP_ALIGN.CENTER)

# Dataset signals
add_text_box(slide, Inches(0.5), Inches(4.5), Inches(9), Inches(0.4),
             "DATASET SIGNALS USED FOR SCORING", font_size=12, bold=True, color=WHITE)

signals = [
    ("blood_group", "ABO + Rh type for compatibility matching"),
    ("frequency_in_days", "Patient's transfusion cadence (seed for EWMA)"),
    ("calls_to_donations_ratio", "Efficiency signal - fewer calls = more reliable"),
    ("donations_till_date", "Volume signal - proven repeat donors score higher"),
    ("last_donation_date", "Recency signal - recent donors rank higher"),
    ("eligibility_status", "Gate: only 'eligible' donors are matched"),
    ("city_id / lat / lng", "Geography: city scoping + haversine proximity"),
]

for i, (field, desc) in enumerate(signals):
    top = Inches(4.9 + i * 0.35)
    add_text_box(slide, Inches(0.7), top, Inches(2.5), Inches(0.35),
                 field, font_size=9, bold=True, color=TEAL)
    add_text_box(slide, Inches(3.3), top, Inches(6.2), Inches(0.35),
                 desc, font_size=9, color=LIGHT_GRAY)


# ==========================================================================
# SLIDE 18: THANK YOU / CLOSING
# ==========================================================================
slide = prs.slides.add_slide(blank_layout)
set_slide_bg(slide, DARK_BG)

add_shape_with_fill(slide, Inches(0), Inches(0), Inches(10), Inches(0.08), LIGHT_ACCENT)
add_shape_with_fill(slide, Inches(0), Inches(7.35), Inches(10), Inches(0.15), DEEP_RED)
add_shape_with_fill(slide, Inches(0.4), Inches(1.5), Inches(0.06), Inches(4.5), TEAL)

add_text_box(slide, Inches(0.8), Inches(2.0), Inches(8), Inches(1.0),
             "Thank You", font_size=48, bold=True, color=WHITE, font_name="Segoe UI Light")

add_text_box(slide, Inches(0.8), Inches(3.2), Inches(8), Inches(0.6),
             "PulseLink - The Blood Subscription for Thalassemia Care",
             font_size=18, color=TEAL)

add_text_box(slide, Inches(0.8), Inches(4.2), Inches(8), Inches(1.5),
             "Every transfusion predicted. Every donor ranked.\n"
             "Every call automated. Every patient reassured.\n"
             "Zero manual effort in the happy path.",
             font_size=14, color=MED_GRAY)

# Live links
add_text_box(slide, Inches(0.8), Inches(5.8), Inches(8), Inches(0.4),
             "Live Demo: https://dpijq2esptlq8.cloudfront.net",
             font_size=12, color=WARM_ORANGE)
add_text_box(slide, Inches(0.8), Inches(6.2), Inches(8), Inches(0.4),
             "API Docs: https://d1u7u2ctmq6ryx.cloudfront.net/docs",
             font_size=12, color=WARM_ORANGE)

# Badge
badge = add_shape_with_fill(slide, Inches(0.8), Inches(6.7), Inches(3.2), Inches(0.5), DEEP_RED,
                            shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)
add_text_box(slide, Inches(0.9), Inches(6.73), Inches(3.0), Inches(0.5),
             "AI for Good Hackathon 2025", font_size=13, bold=True,
             color=WHITE, alignment=PP_ALIGN.CENTER)


# ============================================================================
# SAVE
# ============================================================================
output_path = "/projects/sandbox/PulseLinkAI/PulseLink_Final_Presentation.pptx"
prs.save(output_path)
print(f"Presentation saved to: {output_path}")
print(f"Total slides: {len(prs.slides)}")
