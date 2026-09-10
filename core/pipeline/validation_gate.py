"""
ValidationGate — gate de validation avant livraison.
Trois etats : bloqué, provisoire, valide_selon_protocole.
"""
from __future__ import annotations
from enum import Enum
from typing import Any


class ValidationState(Enum):
    BLOQUE = "bloque"                       # calculs impossibles ou contradictions critiques
    PROVISOIRE = "provisoire"               # resultats partiels, exclusions explicites
    VALIDE_SELON_PROTOCOLE = "valide_selon_protocole"  # controles requis satisfaits


class ValidationGate:
    """Gate de validation avant livraison du metre."""

    def validate(self, reconciliation: dict) -> dict:
        """Valide le metre et determine l'etat de livraison."""
        ecarts = reconciliation.get("ecarts", [])
        stats = reconciliation.get("statistiques", {})

        # Classer les ecarts par severite
        errors = [e for e in ecarts if e.get("severity") == "error"]
        warnings = [e for e in ecarts if e.get("severity") == "warning"]
        infos = [e for e in ecarts if e.get("severity") == "info"]

        # Determiner l'etat
        if errors:
            state = ValidationState.BLOQUE
        elif warnings:
            state = ValidationState.PROVISOIRE
        else:
            state = ValidationState.VALIDE_SELON_PROTOCOLE

        # Verifications specifiques
        checks = self._run_checks(reconciliation)

        return {
            "etat": state.value,
            "checks": checks,
            "errors": errors,
            "warnings": warnings,
            "infos": infos,
            "peut_livrer": state != ValidationState.BLOQUE,
            "resume": self._generate_summary(state, stats, checks),
        }

    def _run_checks(self, reconciliation: dict) -> list[dict]:
        """Lance les controles de validation."""
        checks = []
        stats = reconciliation.get("statistiques", {})
        ecarts = reconciliation.get("ecarts", [])

        # Check 1: Toutes les instances ont des dimensions
        instances_manquantes = stats.get("instances_manquantes", 0)
        checks.append({
            "name": "dimensions_completes",
            "passed": instances_manquantes == 0,
            "detail": f"{instances_manquantes} instances sans dimensions",
        })

        # Check 2: Aucune observation orpheline
        obs_orphelines = sum(1 for e in ecarts if e["type"] == "observation_orpheline")
        checks.append({
            "name": "observations_attachees",
            "passed": obs_orphelines == 0,
            "detail": f"{obs_orphelines} observations non rattachees",
        })

        # Check 3: Toutes les lignes de metre ont des sources
        q_sans_source = sum(1 for e in ecarts if e["type"] == "quantite_sans_source")
        checks.append({
            "name": "sources_tracees",
            "passed": q_sans_source == 0,
            "detail": f"{q_sans_source} quantites sans source",
        })

        # Check 4: Couverture table/instances
        couverture = reconciliation.get("couverture", {})
        pct = couverture.get("couverture_vues", 0)
        checks.append({
            "name": "couverture_table",
            "passed": pct >= 80,
            "detail": f"Couverture vues/tables: {pct}%",
        })

        # Check 5: Pas de conflits
        conflits = stats.get("instances_en_conflit", 0)
        checks.append({
            "name": "pas_de_conflits",
            "passed": conflits == 0,
            "detail": f"{conflits} conflits detectes",
        })

        return checks

    def _generate_summary(self, state: ValidationState,
                           stats: dict, checks: list[dict]) -> str:
        """Genere un resume lisible."""
        passed = sum(1 for c in checks if c["passed"])
        total = len(checks)

        if state == ValidationState.BLOQUE:
            return (
                f"BLOQUE: {total - passed} echec(s) critique(s). "
                f"Le metre ne peut pas etre livre tel quel."
            )
        elif state == ValidationState.PROVISOIRE:
            return (
                f"PROVISOIRE: {passed}/{total} controles OK. "
                f"Resultats partiels avec reserves explicites."
            )
        else:
            return (
                f"VALIDE: {passed}/{total} controles OK. "
                f"Prets pour livraison."
            )
