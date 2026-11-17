from pathlib import Path
import math
import openpyxl
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

POIDS_NOMINAUX_BET = {6: 0.222, 8: 0.395, 10: 0.617, 12: 0.888, 14: 1.208,
                      16: 1.578, 20: 2.466, 25: 3.853, 32: 6.313}

from core.guardrails import valeur_lue


def _lue(obj, cle):
    """Lecture sans defaut invente (RG-6) : None si absent/vide/nul."""
    return valeur_lue(obj, cle)


def _pml(phi):
    """Poids nominal BET (kg/ml), repli phi^2/162."""
    phi = int(phi)
    return POIDS_NOMINAUX_BET.get(phi, (phi ** 2) / 162.0)

# --- CHARTE GRAPHIQUE GÉNIE CIVIL PRESTIGE ---
NAVY = colors.HexColor("#1B365D")        # En-têtes principaux / Titres BET
STEEL = colors.HexColor("#2E5B88")       # Sous-titres de niveau 2
ACCENT = colors.HexColor("#0D9488")      # Visas conformité / Vert Émeraude
BG_LIGHT = colors.HexColor("#F8FAFC")    # Fond neutre technique
BORDER_LINE = colors.HexColor("#CBD5E1") # Filet technique gris mécanique
BORDER_DARK = colors.HexColor("#94A3B8") # Cadre extérieur
MUTED = colors.HexColor("#64748B")       # Textes secondaires / métadonnées


class EngineeringCanvas(canvas.Canvas):
    """Canvas technique à deux passes : trace l'encadrement normalisé et le calcul 'Page X / Y'."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_engineering_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_engineering_decorations(self, total_pages):
        self.saveState()
        w, h = 595.27, 841.89  # Dimensions A4 standard en points
        margin = 36.0

        # 1. Cadre technique extérieur
        self.setStrokeColor(BORDER_DARK)
        self.setLineWidth(1.0)
        self.rect(margin - 12, margin - 12, w - 2 * (margin - 12), h - 2 * (margin - 12))

        # 2. Filet de marge interne
        self.setStrokeColor(BORDER_LINE)
        self.setLineWidth(0.5)
        self.rect(margin - 8, margin - 8, w - 2 * (margin - 8), h - 2 * (margin - 8))

        # 3. En-tête technique permanent
        self.setStrokeColor(NAVY)
        self.setLineWidth(1.2)
        self.line(margin, h - 48, w - margin, h - 48)

        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(NAVY)
        self.drawString(margin, h - 42, "BUREAU D'ÉTUDES TECHNIQUES — STRUCTURES & MÉTRÉ GÉNIE CIVIL")

        self.setFont("Helvetica", 8)
        self.setFillColor(MUTED)
        self.drawRightString(w - margin, h - 42, "RÉF. BET-2026 / EXE-01")

        # 4. Pied de page technique permanent
        self.setStrokeColor(BORDER_LINE)
        self.setLineWidth(0.8)
        self.line(margin, margin + 14, w - margin, margin + 14)

        self.setFont("Helvetica", 7.5)
        self.setFillColor(MUTED)
        self.drawString(margin, margin + 4, "Livrable d'ingénierie PlanBA Metre Agent • Conforme Eurocode 2 (NF EN 1992-1-1) & DTU 20.1")
        self.drawRightString(w - margin, margin + 4, f"Page {self._pageNumber} sur {total_pages}")

        self.restoreState()


def generer_note_calcul_chantier(donnees_plan: dict, out_pdf_path: str):
    doc = SimpleDocTemplate(
        out_pdf_path,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=54,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()

    title_bet = ParagraphStyle(
        'BetTitle',
        fontName='Helvetica-Bold',
        fontSize=13,
        textColor=NAVY,
        leading=16,
        alignment=1,
        spaceAfter=3
    )

    sub_bet = ParagraphStyle(
        'BetSub',
        fontName='Helvetica',
        fontSize=9,
        textColor=MUTED,
        leading=11,
        alignment=1,
        spaceAfter=10
    )

    sec_title = ParagraphStyle(
        'BetSec',
        fontName='Helvetica-Bold',
        fontSize=10,
        textColor=colors.white,
        leading=13
    )

    body_txt = ParagraphStyle(
        'BetBody',
        fontName='Helvetica',
        fontSize=8.5,
        textColor=colors.black,
        leading=11
    )

    bold_txt = ParagraphStyle(
        'BetBold',
        fontName='Helvetica-Bold',
        fontSize=8.5,
        textColor=colors.black,
        leading=11
    )

    table_header = ParagraphStyle(
        'BetTblHead',
        fontName='Helvetica-Bold',
        fontSize=8,
        textColor=NAVY,
        alignment=1
    )

    table_body = ParagraphStyle(
        'BetTblBody',
        fontName='Helvetica',
        fontSize=8,
        textColor=colors.black,
        alignment=1
    )

    story = []

    # =========================================================================
    # 1. EN-TÊTE & TITRE DU DOCUMENT
    # =========================================================================
    story.append(Paragraph("NOTE DE CALCULS D'EXÉCUTION & MÉTRÉ JUSTIFICATIF", title_bet))
    story.append(Paragraph("FONDATIONS SUPERFICIELLES, TERRASSEMENT & NOMENCLATURE DU FERRAILLAGE", sub_bet))

    # =========================================================================
    # 2. CARTOUCHE TECHNIQUE TYPE BET (Normalisé)
    # =========================================================================
    projet_nom = donnees_plan.get("projet", "Projet BTP")
    if isinstance(projet_nom, dict):
        projet_nom = projet_nom.get("nom") or "Projet BTP"
    projet_nom = str(projet_nom)

    def _champ(cle, defaut="—"):
        val = donnees_plan.get(cle, defaut)
        return str(val) if val not in (None, "") else defaut

    import datetime as _dt
    date_doc = donnees_plan.get("date") or _dt.date.today().strftime("%d/%m/%Y")

    cartouche_data = [
        [
            Paragraph("<b>PROJET</b>", body_txt),
            Paragraph(projet_nom, bold_txt),
            Paragraph("<b>DATE</b>", body_txt),
            Paragraph(date_doc, body_txt),
        ],
        [
            Paragraph("<b>LOCALISATION</b>", body_txt),
            Paragraph(_champ("localisation"), body_txt),
            Paragraph("<b>BÂTIMENT</b>", body_txt),
            Paragraph(_champ("batiment"), body_txt),
        ],
        [
            Paragraph("<b>ENTREPRISE</b>", body_txt),
            Paragraph(_champ("entreprise", "Adjudicataire lot GO"), body_txt),
            Paragraph("<b>RÉDACTEUR</b>", body_txt),
            Paragraph(_champ("redacteur", "Technicien Méthodes & Métré"),
                      body_txt),
        ],
        [
            Paragraph("<b>PHASE</b>", body_txt),
            Paragraph("Exécution Chantier (EXE)", body_txt),
            Paragraph("<b>VISA BC / MOE</b>", body_txt),
            Paragraph(_champ("visa", "À viser"), body_txt),
        ],
    ]
    t_cartouche = Table(cartouche_data, colWidths=[120, 160, 110, 133])
    t_cartouche.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), BG_LIGHT),
        ('BOX', (0, 0), (-1, -1), 1.2, NAVY),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, BORDER_LINE),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(t_cartouche)
    story.append(Spacer(1, 8))

    # Table des hypothèses de chantier (mission : q_sol, C25/30, B500B,
    # enrobages différenciés fondation/élévation)
    story.append(Paragraph("HYPOTHÈSES DE CHANTIER RETENUES", sub_bet))
    hyp_data = [
        [Paragraph("<b>Poste</b>", table_header),
         Paragraph("<b>Valeur retenue</b>", table_header)],
        [Paragraph("Sol / contrainte admissible", body_txt),
         Paragraph("q_sol = 1,50 à 2,00 bars (bon sol)", body_txt)],
        [Paragraph("Béton", body_txt),
         Paragraph("C25/30 (fck = 25 MPa), granulat Dmax = 20 mm, "
                   "affaissement S3 (plastique). Propreté 150 kg/m³, "
                   "BA 350 kg/m³", body_txt)],
        [Paragraph("Aciers", body_txt),
         Paragraph("B500B Haute Adhérence (FeE 500)", body_txt)],
        [Paragraph("Enrobage garanti", body_txt),
         Paragraph("5 cm en fondation (contact BP/terre), "
                   "3 cm en élévation", body_txt)],
    ]
    t_hyp = Table(hyp_data, colWidths=[180, 343])
    t_hyp.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_LINE),
        ('BOX', (0, 0), (-1, -1), 1.0, STEEL),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(t_hyp)
    story.append(Spacer(1, 10))

    # =========================================================================
    # 3. SECTION 1 : JUSTIFICATIF DES VOLUMES GÉOMÉTRIQUES (FOUILLES & BÉTONS)
    # =========================================================================
    sec1_banner = Table([[Paragraph("1. JUSTIFICATIF DES VOLUMES GÉOMÉTRIQUES (GROS ŒUVRE)", sec_title)]], colWidths=[523])
    sec1_banner.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), NAVY),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(sec1_banner)
    story.append(Spacer(1, 4))

    desc_geo = (
        "<b>Règles d'exécution appliquées :</b> Purge de fouille avec surlargeur de 10 cm par face pour calage ; "
        "Béton de propreté dosé à 150 kg/m³ (épaisseur = 10 cm, débord = 5 cm) ; Béton armé dosé à 350 kg/m³."
    )
    story.append(Paragraph(desc_geo, body_txt))
    story.append(Spacer(1, 6))

    semelles = donnees_plan.get("semelles", [])
    geo_headers = [
        Paragraph("Repère", table_header),
        Paragraph("Implantation", table_header),
        Paragraph("Coffrage (m)<br/>a x b x h", table_header),
        Paragraph("Fouille (m³)<br/>(+10cm/face)", table_header),
        Paragraph("B.P. (m³)<br/>(e=10cm)", table_header),
        Paragraph("Béton Armé<br/>V (m³)", table_header),
    ]

    geo_rows = [geo_headers]
    tot_fouille = 0.0
    tot_bp = 0.0
    tot_ba = 0.0
    missing_notes: list = []   # elements A RENSEIGNER (ex: note de calcul honnete)

    for s in semelles:
        a, b, h = (_lue(s, "a"), _lue(s, "b"), _lue(s, "h"))
        rep = f"Semelle {s.get('type', 'S')} ({s.get('axe', '?')}-{s.get('file', '?')})"
        if a is None or b is None or h is None:
            missing_notes.append(f"{rep} : a/b/h non lus ({(a, b, h)}) — A RENSEIGNER")
            geo_rows.append([
                Paragraph(f"<b>{rep}</b>", table_body),
                Paragraph(f"Axe {s.get('axe', '')} - File {s.get('file', '')}", table_body),
                Paragraph("A RENSEIGNER", table_body),
                Paragraph("-", table_body),
                Paragraph("-", table_body),
                Paragraph("-", table_body),
            ])
            continue
        v_f = round((a + 0.20) * (b + 0.20) * 1.20, 3)
        v_p = round((a + 0.10) * (b + 0.10) * 0.10, 3)
        v_b = round(a * b * h, 3)
        tot_fouille += v_f
        tot_bp += v_p
        tot_ba += v_b

        geo_rows.append([
            Paragraph(f"<b>Semelle {s.get('type', 'S')}</b>", table_body),
            Paragraph(f"Axe {s.get('axe', '')} - File {s.get('file', '')}", table_body),
            Paragraph(f"{a:.2f} x {b:.2f} x {h:.2f}", table_body),
            Paragraph(f"{v_f:.3f}", table_body),
            Paragraph(f"{v_p:.3f}", table_body),
            Paragraph(f"<b>{v_b:.3f}</b>", table_body),
        ])

    # Fûts / amorce-poteaux : section poteau x hauteur sous longrine
    tfut = 0.0
    for p in donnees_plan.get("poteaux", []):
        a, b, haut = (_lue(p, "a"), _lue(p, "b"), _lue(p, "hauteur"))
        rep = f"Fût {p.get('type', '?')} ({p.get('axe', '?')}-{p.get('file', '?')})"
        if a is None or b is None or haut is None:
            missing_notes.append(f"{rep} : a/b/hauteur non lus ({(a, b, haut)}) — A RENSEIGNER")
            geo_rows.append([
                Paragraph(f"<b>{rep}</b>", table_body),
                Paragraph(f"Axe {p.get('axe', '?')} - File {p.get('file', '?')}",
                          table_body),
                Paragraph("A RENSEIGNER", table_body),
                Paragraph("-", table_body),
                Paragraph("-", table_body),
                Paragraph("-", table_body),
            ])
            continue
        vf = round(a * b * haut, 3)
        tfut += vf
        geo_rows.append([
            Paragraph(f"<b>Fût {p.get('type', '?')}</b>", table_body),
            Paragraph(f"Axe {p.get('axe', '?')} - File {p.get('file', '?')}",
                      table_body),
            Paragraph(f"{a:.2f} x {b:.2f} x {haut:.2f}", table_body),
            Paragraph("-", table_body),
            Paragraph("-", table_body),
            Paragraph(f"<b>{vf:.3f}</b>", table_body),
        ])

    _part = f" (PARTIEL — {len(missing_notes)} type(s) A RENSEIGNER)" if missing_notes else ""
    geo_rows.append([
        Paragraph(f"<b>TOTAL BRUT CHANTIER{_part}</b>", table_header),
        Paragraph("-", table_body),
        Paragraph("-", table_body),
        Paragraph(f"<b>{tot_fouille:.3f} m³</b>", table_header),
        Paragraph(f"<b>{tot_bp:.3f} m³</b>", table_header),
        Paragraph(f"<b>{tot_ba + tfut:.3f} m³</b>", table_header),
    ])
    geo_rows.append([
        Paragraph(f"<b>TOTAL +3% PERTES BÉTON{_part}</b>", table_header),
        Paragraph("-", table_body),
        Paragraph("-", table_body),
        Paragraph(f"<b>{tot_fouille * 1.03:.2f} m³</b>", table_header),
        Paragraph(f"<b>{tot_bp * 1.03:.2f} m³</b>", table_header),
        Paragraph(f"<b>{(tot_ba + tfut) * 1.03:.2f} m³</b>", table_header),
    ])

    t_geo = Table(geo_rows, colWidths=[90, 100, 103, 75, 75, 80])
    t_geo.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), BG_LIGHT),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_LINE),
        ('BOX', (0, 0), (-1, -1), 1.0, STEEL),
        ('BACKGROUND', (0, -1), (-1, -1), BG_LIGHT),
        ('LINEABOVE', (0, -1), (-1, -1), 1.0, STEEL),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(t_geo)
    story.append(Spacer(1, 10))

    # =========================================================================
    # 4. SECTION 2 : NOMENCLATURE DU FERRAILLAGE & BORDEREAU DE COUPE
    # =========================================================================
    sec2_banner = Table([[Paragraph("2. DÉCORTICAGE D'ATELIER & BORDEREAU DE COUPE DES ACIERS", sec_title)]], colWidths=[523])
    sec2_banner.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), NAVY),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(sec2_banner)
    story.append(Spacer(1, 4))

    desc_acier = (
        "<b>Formule d'épure de façonnage :</b> Longueur de barre développée L = (Cote hors-tout - 2 * Enrobage 5 cm) "
        "+ 2 retours à 135° (ancrage forfaitaire 34 Ø) selon Eurocode 2 §8.4."
    )
    story.append(Paragraph(desc_acier, body_txt))
    story.append(Spacer(1, 6))

    acier_headers = [
        Paragraph("Repère", table_header),
        Paragraph("Nappe / Disposition", table_header),
        Paragraph("Barres", table_header),
        Paragraph("Ø (mm)", table_header),
        Paragraph("L. coupe (m)", table_header),
        Paragraph("Linéaire (ml)", table_header),
        Paragraph("Poids (kg)", table_header),
    ]
    poids_unit = {6: 0.222, 8: 0.395, 10: 0.617, 12: 0.888, 14: 1.208, 16: 1.578, 20: 2.466}
    acier_rows = [acier_headers]
    tot_acier_kg = 0.0
    tot_sem_kg = 0.0

    for s in semelles:
        a = _lue(s, "a"); b = _lue(s, "b")
        phi = _lue(s, "phi")
        nb_x = _lue(s, "nb_x")
        # nb_y absent en general : literie isotopique, fallback nb_x (documenté)
        nb_y_raw = s.get("nb_y")
        nb_y = nb_y_raw if nb_y_raw not in (None, 0, "", False) else nb_x
        rep = f"Semelle {s.get('type', 'S')} ({s.get('axe', '?')}-{s.get('file', '?')})"
        if a is None or b is None or phi is None or nb_x is None or nb_y is None:
            missing_notes.append(
                f"{rep} : ferraillage a/b/phi/nb_x non lus "
                f"({(a, b, phi, nb_x, nb_y)}) — A RENSEIGNER")
            continue
        phi = int(phi); nb_x = int(nb_x); nb_y = int(nb_y)
        pml = _pml(phi)

        # Nappe X — formule chantier : L = (cote - 2x enrobage 5cm) + 34xphi
        lx = round((a - 2 * 0.05) + 34 * phi / 1000, 3)
        lin_x = round(nb_x * lx, 2)
        pds_x = round(lin_x * pml, 2)
        tot_acier_kg += pds_x
        tot_sem_kg += pds_x
        acier_rows.append([
            Paragraph(f"Semelle {s.get('type', 'S')}", table_body),
            Paragraph("Lit Inférieur X", table_body),
            Paragraph(str(nb_x), table_body),
            Paragraph(f"HA{phi}", table_body),
            Paragraph(f"{lx:.3f}", table_body),
            Paragraph(f"{lin_x:.2f}", table_body),
            Paragraph(f"{pds_x:.2f}", table_body),
        ])

        # Nappe Y — même formule chantier sur la largeur b
        ly = round((b - 2 * 0.05) + 34 * phi / 1000, 3)
        lin_y = round(nb_y * ly, 2)
        pds_y = round(lin_y * pml, 2)
        tot_acier_kg += pds_y
        tot_sem_kg += pds_y
        acier_rows.append([
            Paragraph(f"Semelle {s.get('type', 'S')}", table_body),
            Paragraph("Lit Inférieur Y", table_body),
            Paragraph(str(nb_y), table_body),
            Paragraph(f"HA{phi}", table_body),
            Paragraph(f"{ly:.3f}", table_body),
            Paragraph(f"{lin_y:.2f}", table_body),
            Paragraph(f"{pds_y:.2f}", table_body),
        ])

    # --- Poteaux : ARM LONG + cadres (périmètre développé) ---
    tot_pot_kg = 0.0
    for p in donnees_plan.get("poteaux", []):
        lab = str(p.get("type", "?"))
        a = _lue(p, "a"); b = _lue(p, "b"); haut = _lue(p, "hauteur")
        rep = f"Fût {lab} ({p.get('axe', '?')}-{p.get('file', '?')})"
        if a is None or b is None or haut is None:
            missing_notes.append(
                f"{rep} : a/b/hauteur non lus ({(a, b, haut)}) — A RENSEIGNER (ferraillage ignoré)")
            continue
        for lb in p.get("long_bars", []) or []:
            if (_lue(lb, "nb") or 0) > 0 and (_lue(lb, "phi") or 0) > 0:
                l_unit = round(haut + 0.50, 3)
                lin = round(lb["nb"] * l_unit, 2)
                pds = round(lin * _pml(lb["phi"]), 2)
                tot_acier_kg += pds
                tot_pot_kg += pds
                acier_rows.append([
                    Paragraph(lab, table_body),
                    Paragraph("ARM LONG", table_body),
                    Paragraph(str(lb["nb"]), table_body),
                    Paragraph(f"HA{lb['phi']}", table_body),
                    Paragraph(f"{l_unit:.3f}", table_body),
                    Paragraph(f"{lin:.2f}", table_body),
                    Paragraph(f"{pds:.2f}", table_body),
                ])
        cad = p.get("cadres", {}) or {}
        if _lue(cad, "phi") and (_lue(cad, "esp") or 0) > 0:
            phi_c = cad["phi"]
            ancrage = max(10 * phi_c / 1000.0, 0.10)
            perim = round(2 * (a + b) - 8 * 0.05 + 2 * ancrage, 3)
            nb_c = int(math.ceil(haut / cad["esp"])) + 2
            lin = round(nb_c * perim, 2)
            pds = round(lin * _pml(phi_c), 2)
            tot_acier_kg += pds
            tot_pot_kg += pds
            acier_rows.append([
                Paragraph(lab, table_body),
                Paragraph(f"CADRE T{phi_c} e={cad['esp'] * 100:.0f}",
                          table_body),
                Paragraph(str(nb_c), table_body),
                Paragraph(f"HA{phi_c}", table_body),
                Paragraph(f"{perim:.3f}", table_body),
                Paragraph(f"{lin:.2f}", table_body),
                Paragraph(f"{pds:.2f}", table_body),
            ])

    # --- Poutres : filants + cadres (portée connue uniquement) ---
    poutres_sans_portee = 0
    for p in donnees_plan.get("poutres", []):
        lab = f"Poutre {p.get('type', '?')}"
        b = _lue(p, "b"); h = _lue(p, "h")
        if b is None or h is None:
            missing_notes.append(
                f"Poutre {p.get('type', '?')} : b/h non lus ({(b, h)}) — A RENSEIGNER (ferraillage ignoré)")
            continue
        portee = p.get("portee")
        for fi in (p.get("filants_inf", []) or []) + \
                (p.get("filants_sup", []) or []):
            if fi.get("nb", 0) <= 0 or fi.get("phi", 0) <= 0:
                continue
            if not portee:
                poutres_sans_portee += 1
                continue
            l_unit = round(portee + 0.50, 3)
            lin = round(fi["nb"] * l_unit, 2)
            pds = round(lin * _pml(fi["phi"]), 2)
            tot_acier_kg += pds
            acier_rows.append([
                Paragraph(lab, table_body),
                Paragraph("Filants", table_body),
                Paragraph(str(fi["nb"]), table_body),
                Paragraph(f"HA{fi['phi']}", table_body),
                Paragraph(f"{l_unit:.3f}", table_body),
                Paragraph(f"{lin:.2f}", table_body),
                Paragraph(f"{pds:.2f}", table_body),
            ])
        cad = p.get("cadres", {}) or {}
        if cad.get("phi", 0) > 0 and (cad.get("esp") or 0) > 0 and portee:
            phi_c = cad["phi"]
            ancrage = max(10 * phi_c / 1000.0, 0.10)
            perim = round(2 * (b + h) - 8 * 0.05 + 2 * ancrage, 3)
            nb_c = int(math.ceil(portee / cad["esp"])) + 2
            lin = round(nb_c * perim, 2)
            pds = round(lin * _pml(phi_c), 2)
            tot_acier_kg += pds
            acier_rows.append([
                Paragraph(lab, table_body),
                Paragraph(f"CADRE T{phi_c}", table_body),
                Paragraph(str(nb_c), table_body),
                Paragraph(f"HA{phi_c}", table_body),
                Paragraph(f"{perim:.3f}", table_body),
                Paragraph(f"{lin:.2f}", table_body),
                Paragraph(f"{pds:.2f}", table_body),
            ])

    chutes_kg = tot_acier_kg * 0.05
    commande_kg = tot_acier_kg * 1.05

    acier_rows.append([
        Paragraph("<b>TOTAL THÉORIQUE NET</b>", table_header),
        Paragraph("-", table_body),
        Paragraph("-", table_body),
        Paragraph("-", table_body),
        Paragraph("-", table_body),
        Paragraph("-", table_body),
        Paragraph(f"<b>{tot_acier_kg:.2f} kg</b>", table_header),
    ])
    acier_rows.append([
        Paragraph("<b>COMMANDE ACIER (+5% chutes)</b>", table_header),
        Paragraph("-", table_body),
        Paragraph("-", table_body),
        Paragraph("-", table_body),
        Paragraph("-", table_body),
        Paragraph("-", table_body),
        Paragraph(f"<b>{commande_kg:.2f} kg</b>", table_header),
    ])

    t_acier = Table(acier_rows, colWidths=[85, 95, 50, 48, 75, 80, 90])
    t_acier.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), BG_LIGHT),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_LINE),
        ('BOX', (0, 0), (-1, -1), 1.0, STEEL),
        ('BACKGROUND', (0, -2), (-1, -1), BG_LIGHT),
        ('LINEABOVE', (0, -2), (-1, -1), 1.0, STEEL),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(t_acier)
    story.append(Spacer(1, 10))

    # =========================================================================
    # 5. SECTION 3 : INDICATEURS TECHNIQUES & VISA DE DIRECTION DES TRAVAUX
    # =========================================================================
    sec3_banner = Table([[Paragraph("3. RATIOS DE CONTRÔLE BET & VISA D'EXÉCUTION", sec_title)]], colWidths=[523])
    sec3_banner.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), NAVY),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(sec3_banner)
    story.append(Spacer(1, 6))

    v_ba_total = tot_ba + tfut
    ratio_m3 = tot_acier_kg / v_ba_total if v_ba_total > 0 else 0.0
    ratio_sem = tot_sem_kg / tot_ba if tot_ba > 0 else 0.0
    ratio_pot = tot_pot_kg / tfut if tfut > 0 else 0.0

    def _statut(val, mini, maxi):
        tag = "CONFORME" if mini <= val <= maxi else "À VÉRIFIER"
        return f"Plage cible : {mini} à {maxi} kg/m³ (<b>{tag}</b>)"

    synth_data = [
        [
            Paragraph("<b>RATIO SEMELLES :</b>", body_txt),
            Paragraph(f"<b>{ratio_sem:.1f} kg / m³ de béton coulé</b>",
                      bold_txt),
            Paragraph(_statut(ratio_sem, 35, 50), body_txt),
        ],
        [
            Paragraph("<b>RATIO POTEAUX / AMORCES :</b>", body_txt),
            Paragraph(f"<b>{ratio_pot:.1f} kg / m³ de béton coulé</b>"
                      if tfut > 0 else "<b>—</b> (pas de fût chiffré)",
                      bold_txt),
            Paragraph(_statut(ratio_pot, 80, 120)
                      if tfut > 0 else "Sans objet", body_txt),
        ],
        [
            Paragraph("<b>RATIO GLOBAL FONDATIONS :</b>", body_txt),
            Paragraph(f"<b>{ratio_m3:.1f} kg / m³</b> (poids total acier / "
                      f"volume total béton BA : {v_ba_total:.2f} m³)",
                      bold_txt),
            Paragraph("Indicateur de rentabilité chantier", body_txt),
        ],
        [
            Paragraph("<b>TONNAGE TOTAL DU LOT :</b>", body_txt),
            Paragraph(f"<b>{tot_acier_kg / 1000:.3f} Tonnes (Net)</b>",
                      bold_txt),
            Paragraph(f"Facturation fournisseur (+5% chutes) : "
                      f"<b>{commande_kg / 1000:.3f} T</b>", body_txt),
        ],
        [
            Paragraph("<b>DÉCISION DU CONTRÔLE TECHNIQUE :</b>", body_txt),
            (Paragraph(
                "<font color='#B91C1C'><b>VISA À RENSEIGNER — "
                "ÉLÉMENTS INCOMPLETS</b></font> "
                "<i>(un métré incomplet n'est pas un métré ; "
                "relire la fondation et renseigner les côtes)</i>",
                bold_txt) if missing_notes else
             Paragraph(
                "<font color='#0D9488'><b>BON POUR EXÉCUTION (BPE)</b></font> "
                "<i>(sous réserve des tolérances Eurocode 2 — "
                "bon de commande usine / ferrailleur)</i>",
                bold_txt)),
            (Paragraph("Voir avertissements ci-après", body_txt) if missing_notes
             else Paragraph("Armatures certifiées NF AFCAB / B500B", body_txt)),
        ]
    ]
    t_synth = Table(synth_data, colWidths=[180, 160, 183])
    t_synth.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), BG_LIGHT),
        ('BOX', (0, 0), (-1, -1), 1.0, ACCENT),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, BORDER_LINE),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(t_synth)

    # Avertissements honnetes: CHAQUE element A RENSEIGNER
    if missing_notes:
        story.append(Spacer(1, 6))
        story.append(Paragraph("<b>AVERTISSEMENTS — ÉLÉMENTS À RENSEIGNER</b>",
                              bold_txt))
        for n in missing_notes:
            story.append(Paragraph("• " + n, body_txt))

    # Construction du document avec le canvas technique d'ingénierie
    Path(out_pdf_path).parent.mkdir(parents=True, exist_ok=True)
    doc.build(story, canvasmaker=EngineeringCanvas)
    print(f"Note de calculs PDF chantier generee : {out_pdf_path}")
    return out_pdf_path


if __name__ == "__main__":
    sample = {
        "projet": "Projet R+2 MZINDA",
        "semelles": [
            {"type": "S1", "axe": "A", "file": "1", "a": 1.2, "b": 1.2, "h": 0.3, "phi": 12, "nb_x": 8, "nb_y": 8},
            {"type": "S2", "axe": "B", "file": "2", "a": 1.5, "b": 1.5, "h": 0.4, "phi": 12, "nb_x": 10, "nb_y": 10}
        ]
    }
    generer_note_calcul_chantier(sample, "output/note_calculs_chantier.pdf")
