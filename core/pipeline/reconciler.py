"""
Reconciler — verification zero-miss.
Compare observations, instances et lignes de metre.
Rapport d'ecart obligatoire.
"""
from __future__ import annotations
from typing import Any
from .evidence_store import EvidenceStore, PhysicalInstance


class Reconciler:
    """Reconciliation complete : zero omission masquee."""

    def reconcile(self, store: EvidenceStore) -> dict:
        """Lance toutes les verifications et retourne le rapport."""
        report = {
            "statistiques": {},
            "ecarts": [],
            "avertissements": [],
            "couverture": {},
        }

        # 1. Comptage
        stats = self._compute_stats(store)
        report["statistiques"] = stats

        # 2. Verification observation -> instance
        obs_ecarts = self._check_obs_to_instance(store)
        report["ecarts"].extend(obs_ecarts)

        # 3. Verification instance -> attributs
        attr_ecarts = self._check_instance_attributes(store)
        report["ecarts"].extend(attr_ecarts)

        # 4. Verification instance -> postes attendus
        postes_ecarts = self._check_expected_postes(store)
        report["ecarts"].extend(postes_ecarts)

        # 5. Verification quantite -> sources
        quant_ecarts = self._check_quantity_sources(store)
        report["ecarts"].extend(quant_ecarts)

        # 6. Verification representations entre vues
        vue_ecarts = self._check_cross_view(store)
        report["ecarts"].extend(vue_ecarts)

        # 7. Couverture
        report["couverture"] = self._compute_coverage(store)

        # 8. Avertissements legende
        report["avertissements"] = self._check_legend_warnings(store)

        return report

    def _compute_stats(self, store: EvidenceStore) -> dict:
        """Calcule les statistiques de base."""
        total_instances = len(store.instances)
        complete = sum(1 for i in store.instances if i.statut == "complet")
        manquants = sum(1 for i in store.instances if i.statut == "manquant")
        ambigus = sum(1 for i in store.instances if i.statut == "ambigu")
        conflits = sum(1 for i in store.instances if i.statut == "conflit")

        return {
            "total_observations": len(store.observations),
            "total_instances": total_instances,
            "total_lignes_metre": len(store.metre_lines),
            "instances_completes": complete,
            "instances_manquantes": manquants,
            "instances_ambigues": ambigus,
            "instances_en_conflit": conflits,
            "total_tables": len(store.tables),
            "total_warnings": len(store.warnings),
        }

    def _check_obs_to_instance(self, store: EvidenceStore) -> list[dict]:
        """Verifie que chaque observation est rattachee a une instance."""
        ecarts = []
        attached_obs = set()
        for inst in store.instances:
            for obs_id in inst.observations:
                attached_obs.add(obs_id)

        for obs in store.observations:
            if obs.obs_id not in attached_obs:
                ecarts.append({
                    "type": "observation_orpheline",
                    "severity": "warning",
                    "obs_id": obs.obs_id,
                    "text": obs.text[:100],
                    "description": f"Observation '{obs.text[:50]}' non rattachee a une instance",
                })

        return ecarts

    def _check_instance_attributes(self, store: EvidenceStore) -> list[dict]:
        """Verifie que chaque instance a ses attributs essentiels."""
        ecarts = []
        for inst in store.instances:
            missing = []
            if "a" not in inst.dimensions or "b" not in inst.dimensions:
                missing.append("section")
            if not inst.ferraillage:
                missing.append("ferraillage")

            if missing:
                ecarts.append({
                    "type": "attribut_manquant",
                    "severity": "warning" if len(missing) == 1 else "error",
                    "repere": inst.repere,
                    "instance_id": inst.instance_id,
                    "missing": missing,
                    "description": f"Instance '{inst.repere}' manque: {', '.join(missing)}",
                })

        return ecarts

    def _check_expected_postes(self, store: EvidenceStore) -> list[dict]:
        """Verifie que les postes de metre attendus sont produits."""
        ecarts = []
        metre_reperes = {m.repere for m in store.metre_lines}

        for inst in store.instances:
            if inst.repere not in metre_reperes:
                ecarts.append({
                    "type": "poste_manquant",
                    "severity": "error",
                    "repere": inst.repere,
                    "description": f"Instance '{inst.repere}' sans ligne de metre",
                })

        return ecarts

    def _check_quantity_sources(self, store: EvidenceStore) -> list[dict]:
        """Verifie que les quantites ont des sources tracees."""
        ecarts = []
        for line in store.metre_lines:
            if line.quantite is not None and not line.sources:
                ecarts.append({
                    "type": "quantite_sans_source",
                    "severity": "error",
                    "repere": line.repere,
                    "description": f"Quantite pour '{line.repere}' sans source tracee",
                })

        return ecarts

    def _check_cross_view(self, store: EvidenceStore) -> list[dict]:
        """Verifie les representations entre vues (double potentiel)."""
        ecarts = []
        # Grouper par repere et compter par vue
        rep_vues = {}
        for inst in store.instances:
            rep_vues.setdefault(inst.repere, set()).add(inst.vue)

        for rep, vues in rep_vues.items():
            if len(vues) > 1:
                ecarts.append({
                    "type": "representation_multi_vues",
                    "severity": "info",
                    "repere": rep,
                    "vues": sorted(vues),
                    "description": f"Repere '{rep}' present dans {len(vues)} vues: {', '.join(sorted(vues))}",
                })

        return ecarts

    def _compute_coverage(self, store: EvidenceStore) -> dict:
        """Calcule la couverture du metre."""
        table_reperes = store.get_table_reperes()
        instance_reperes = {i.repere for i in store.instances}
        metre_reperes = {m.repere for m in store.metre_lines}

        return {
            "table_reperes": len(table_reperes),
            "instance_reperes": len(instance_reperes),
            "metre_reperes": len(metre_reperes),
            "couverture_vues": round(
                len(instance_reperes & table_reperes) / max(len(table_reperes), 1) * 100, 1
            ),
        }

    def _check_legend_warnings(self, store: EvidenceStore) -> list[str]:
        """Verifie les avertissements lies a la legende."""
        warnings = []
        fallback_count = sum(
            1 for e in store.legend.values()
            if hasattr(e, 'source_type') and e.source_type.value == "fallback"
        )
        if fallback_count > 0:
            warnings.append(
                f"{fallback_count} prefixes utilises en fallback "
                f"(non confirms sur ce plan)"
            )

        return warnings
