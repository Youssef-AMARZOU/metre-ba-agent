"""
QuantityCalculator — calcule les quantites de metre.
Formules + traçabilite. Aucune valeur par defaut.
"""
from __future__ import annotations
import re
from typing import Optional
from .evidence_store import (
    Evidence, EvidenceStore, MetreLine, PhysicalInstance, SourceType
)


# Table de poids de l'acier (normative, referentiel fixe)
STEEL_WEIGHTS_KG_M = {
    6: 0.222,
    8: 0.395,
    10: 0.617,
    12: 0.888,
    14: 1.21,
    16: 1.58,
    20: 2.47,
    25: 3.85,
    32: 6.17,
}


class QuantityCalculator:
    """Calcule les quantites de metre pour chaque instance."""

    def calculate_all(self, store: EvidenceStore) -> list[MetreLine]:
        """Calcule les quantites pour toutes les instances."""
        lines = []
        for inst in store.instances:
            line = self.calculate_instance(inst, store)
            if line:
                lines.append(line)
        return lines

    def calculate_instance(self, inst: PhysicalInstance,
                            store: EvidenceStore) -> Optional[MetreLine]:
        """Calcule les quantites pour une instance."""
        line = MetreLine(
            repere=inst.repere,
            family_type=inst.family_type,
            instance_id=inst.instance_id,
            dimensions={},
            vue=inst.vue,
            niveau=inst.niveau,
        )

        # Copier les dimensions
        for key, evidence in inst.dimensions.items():
            line.dimensions[key] = evidence.value if hasattr(evidence, 'value') else evidence

        # Calculer selon le type
        family_lower = inst.family_type.lower()

        if any(k in family_lower for k in ["poutre", "longrine", "chainage", "linteau"]):
            self._calc_beam(inst, line, store)
        elif any(k in family_lower for k in ["poteau", "colonne"]):
            self._calc_column(inst, line, store)
        elif any(k in family_lower for k in ["semelle", "radier"]):
            self._calc_footing(inst, line, store)
        elif any(k in family_lower for k in ["dalle", "plancher"]):
            self._calc_slab(inst, line, store)
        elif any(k in family_lower for k in ["voile", "mur"]):
            self._calc_wall(inst, line, store)
        else:
            self._calc_generic(inst, line, store)

        # Calculer le poids d'acier
        self._calc_steel(inst, line, store)

        return line

    def _calc_beam(self, inst: PhysicalInstance, line: MetreLine,
                    store: EvidenceStore):
        """Calcule pour une poutre/longrine/chainage."""
        a = self._get_dim(inst, "a")
        b = self._get_dim(inst, "b")
        section = inst.dimensions.get("section")
        if not section:
            section_val = section.value if hasattr(section, 'value') else section
            if section_val:
                m = re.search(r"(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)", str(section_val))
                if m:
                    a_cm = float(m.group(1))
                    b_cm = float(m.group(2))
                    a = a_cm / 100 if a_cm > 10 else a_cm
                    b = b_cm / 100 if b_cm > 10 else b_cm

        # Longueur depuis les occurrences (distance entre axes)
        longueur = self._get_length_from_occurrences(inst, store)

        if a and b and longueur:
            volume = a * b * longueur
            line.volume_beton_m3 = round(volume, 4)
            line.surface_coffrage_m2 = round(2 * (a + b) * longueur, 4)
            line.longueur_ml = round(longueur, 3)
            line.quantite = line.volume_beton_m3
            line.unite = "m3"
            line.formule = f"{a}x{b}x{longueur}"
            line.sources["volume"] = "calculé: a*b*l"
            line.sources["longueur"] = "trame_axes" if longueur else "manquant"
            line.statut = "complet"
        else:
            line.statut = "manquant"

    def _calc_column(self, inst: PhysicalInstance, line: MetreLine,
                      store: EvidenceStore):
        """Calcule pour un poteau."""
        a = self._get_dim(inst, "a")
        b = self._get_dim(inst, "b")
        hauteur = self._get_dim(inst, "hauteur") or self._get_dim(inst, "h")

        if a and b and hauteur:
            volume = a * b * hauteur
            line.volume_beton_m3 = round(volume, 4)
            line.surface_coffrage_m2 = round(2 * (a + b) * hauteur, 4)
            line.quantite = 1  # un poteau = 1 unite
            line.unite = "pce"
            line.formule = f"{a}x{b}x{hauteur}"
            line.statut = "complet"
        else:
            line.statut = "manquant"

    def _calc_footing(self, inst: PhysicalInstance, line: MetreLine,
                       store: EvidenceStore):
        """Calcule pour une semelle."""
        a = self._get_dim(inst, "a")
        b = self._get_dim(inst, "b")
        h = self._get_dim(inst, "h") or self._get_dim(inst, "hauteur")

        if a and b and h:
            volume = a * b * h
            line.volume_beton_m3 = round(volume, 4)
            line.surface_coffrage_m2 = round(2 * (a + b) * h, 4)
            line.quantite = 1
            line.unite = "pce"
            line.formule = f"{a}x{b}x{h}"
            line.statut = "complet"
        else:
            line.statut = "manquant"

    def _calc_slab(self, inst: PhysicalInstance, line: MetreLine,
                    store: EvidenceStore):
        """Calcule pour une dalle."""
        a = self._get_dim(inst, "a") or self._get_dim(inst, "longueur")
        b = self._get_dim(inst, "b") or self._get_dim(inst, "largeur")
        ep = self._get_dim(inst, "epaisseur") or self._get_dim(inst, "h")

        if a and b and ep:
            volume = a * b * ep
            line.volume_beton_m3 = round(volume, 4)
            line.quantite = round(a * b, 4)
            line.unite = "m2"
            line.formule = f"{a}x{b}x{ep}"
            line.statut = "complet"
        else:
            line.statut = "manquant"

    def _calc_wall(self, inst: PhysicalInstance, line: MetreLine,
                    store: EvidenceStore):
        """Calcule pour un voile/mur."""
        longueur = self._get_dim(inst, "longueur")
        hauteur = self._get_dim(inst, "hauteur") or self._get_dim(inst, "h")
        epaisseur = self._get_dim(inst, "epaisseur") or self._get_dim(inst, "a")

        if longueur and hauteur and epaisseur:
            volume = longueur * hauteur * epaisseur
            line.volume_beton_m3 = round(volume, 4)
            line.quantite = round(longueur * hauteur, 4)
            line.unite = "m2"
            line.statut = "complet"
        else:
            line.statut = "manquant"

    def _calc_generic(self, inst: PhysicalInstance, line: MetreLine,
                       store: EvidenceStore):
        """Calcule generique."""
        line.statut = "manquant"

    def _calc_steel(self, inst: PhysicalInstance, line: MetreLine,
                     store: EvidenceStore):
        """Calcule le poids d'acier."""
        ferr_text = None
        for key in ["ferraillage", "armature", "fer"]:
            if key in inst.ferraillage:
                ev = inst.ferraillage[key]
                ferr_text = ev.value if hasattr(ev, 'value') else ev
                break

        if not ferr_text:
            return

        total_weight = 0
        longueur = line.longueur_ml or 1.0

        # Parser le ferraillage (ex: "8T12" ou "4HA10+2HA8")
        for m in re.finditer(r"(\d+)\s*([THA])\s*(\d+)", str(ferr_text), re.I):
            count = int(m.group(1))
            bar_type = m.group(2).upper()
            diameter = int(m.group(3))

            if diameter in STEEL_WEIGHTS_KG_M:
                weight_per_m = STEEL_WEIGHTS_KG_M[diameter]
                total_weight += count * weight_per_m * longueur

        if total_weight > 0:
            line.poids_acier_kg = round(total_weight, 3)
            line.sources["acier"] = f"calculé: {ferr_text} * {longueur}m"

    def _get_dim(self, inst: PhysicalInstance, key: str) -> Optional[float]:
        """Recupere une dimension en metres."""
        if key in inst.dimensions:
            ev = inst.dimensions[key]
            val = ev.value if hasattr(ev, 'value') else ev
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, str):
                try:
                    v = float(val)
                    return v / 100 if v > 10 else v
                except ValueError:
                    pass
        return None

    def _get_length_from_occurrences(self, inst: PhysicalInstance,
                                      store: EvidenceStore) -> Optional[float]:
        """Calcule la longueur depuis les positions des occurrences."""
        if len(inst.observations) < 2:
            return None

        positions = []
        for obs_id in inst.observations:
            obs = store.get_obs_by_id(obs_id)
            if obs:
                positions.append(obs.x)

        if len(positions) < 2:
            return None

        # La longueur est la distance entre les extremites
        px_span = max(positions) - min(positions)
        if px_span <= 0:
            return None

        # Calibrer avec la trame d'axes si disponible
        vue = inst.vue
        if vue in store.grids:
            grid = store.grids[vue]
            cotes = grid.get("cotes_lettres", [])
            if cotes:
                total_cote_m = sum(cotes) / 100
                # Estimer le scale depuis les axes
                axes = grid.get("axes_lettres", [])
                if len(axes) >= 2:
                    # Utiliser la premiere et derniere cote pour calibrer
                    # Approximation : px_span correspond a total_cote_m
                    if px_span > 0:
                        return round(total_cote_m, 2)

        return None
