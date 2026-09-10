"""
EntityResolver — lie les observations aux instances physiques.
Resout la correspondance: observations -> instances -> representations.
"""
from __future__ import annotations
import re
from typing import Optional
from .evidence_store import (
    Evidence, EvidenceStore, Observation, PhysicalInstance, SourceType
)


class EntityResolver:
    """Resout les correspondances entre observations et instances physiques."""

    def resolve(self, store: EvidenceStore) -> list[PhysicalInstance]:
        """Resout toutes les observations en instances physiques."""
        instances = []

        # Grouper les observations par repere
        obs_by_repere = {}
        for obs in store.observations:
            repere = obs.raw_data.get("repere", "")
            if repere:
                obs_by_repere.setdefault(repere, []).append(obs)

        instance_counter = 0
        for repere, obs_list in obs_by_repere.items():
            # Determiner le type via la legende
            prefix = obs_list[0].raw_data.get("prefix", "")
            family_type = "inconnu"
            if prefix in store.legend:
                family_type = store.legend[prefix].value

            # Creer une instance pour chaque occurrence geographique distincte
            # Deux observations sont le meme element si elles sont proches
            clusters = self._cluster_observations(obs_list)

            for cluster in clusters:
                inst = PhysicalInstance(
                    instance_id=f"inst_{instance_counter:04d}",
                    repere=repere,
                    family_type=family_type,
                    observations=[o.obs_id for o in cluster],
                    vue=self._determine_vue(cluster),
                    niveau=self._determine_niveau(cluster),
                )

                # Enrichir avec les donnees extraites
                self._enrich_instance(inst, cluster, store)

                instances.append(inst)
                instance_counter += 1

        # Regrouper par (repere, family_type) pour dedupliquer
        seen = {}
        unique_instances = []
        for inst in instances:
            key = (inst.repere, inst.family_type)
            if key not in seen:
                seen[key] = inst
                unique_instances.append(inst)
            else:
                # Fusionner les observations
                existing = seen[key]
                existing.observations.extend(inst.observations)
        return unique_instances

    def _cluster_observations(self, obs_list: list[Observation]) -> list[list[Observation]]:
        """Groupe les observations du meme element physique.
        Deux observations sont le meme element si elles sont proches
        spatialement (meme zone de la vue).
        """
        if not obs_list:
            return []

        # Trier par page puis position
        sorted_obs = sorted(obs_list, key=lambda o: (o.page, o.y, o.x))

        clusters = []
        current_cluster = [sorted_obs[0]]

        for i in range(1, len(sorted_obs)):
            prev = sorted_obs[i - 1]
            curr = sorted_obs[i]

            # Meme page et proche spatialement
            same_page = curr.page == prev.page
            close = (
                abs(curr.x - prev.x) < 400 and
                abs(curr.y - prev.y) < 400
            )

            if same_page and close:
                current_cluster.append(curr)
            else:
                clusters.append(current_cluster)
                current_cluster = [curr]

        clusters.append(current_cluster)
        return clusters

    def _enrich_instance(self, inst: PhysicalInstance,
                          obs_list: list[Observation],
                          store: EvidenceStore):
        """Enrichit une instance avec les donnees disponibles."""
        # Dimensions depuis les annotations inline
        for obs in obs_list:
            section = obs.raw_data.get("section_inline")
            if section:
                parts = section.split("x")
                if len(parts) == 2:
                    try:
                        a = float(parts[0]) / 100  # cm -> m
                        b = float(parts[1]) / 100
                        inst.dimensions["section"] = Evidence(
                            value=f"{parts[0]}x{parts[1]}",
                            source_type=SourceType.LU,
                            source_location=f"annotation/{obs.obs_id}",
                            page=obs.page,
                            x=obs.x,
                            y=obs.y,
                        )
                        inst.dimensions["a"] = Evidence(
                            value=a,
                            source_type=SourceType.CALCULE,
                            source_location=f"annotation/{obs.obs_id}/a",
                            page=obs.page,
                        )
                        inst.dimensions["b"] = Evidence(
                            value=b,
                            source_type=SourceType.CALCULE,
                            source_location=f"annotation/{obs.obs_id}/b",
                            page=obs.page,
                        )
                    except ValueError:
                        pass

        # Chercher dans les tableaux
        for table_name, table_data in store.tables.items():
            if inst.repere in table_data:
                table_row = table_data[inst.repere]
                for col_name, evidence in table_row.items():
                    if col_name not in inst.dimensions:
                        inst.dimensions[col_name] = evidence
                    if col_name in ("ferraillage", "armature", "fer"):
                        inst.ferraillage[col_name] = evidence

        # Verifier la complétude
        missing = []
        if "a" not in inst.dimensions or "b" not in inst.dimensions:
            missing.append("section")
        if not inst.ferraillage:
            missing.append("ferraillage")

        if missing:
            inst.statut = "manquant"

    def _determine_vue(self, obs_list: list[Observation]) -> str:
        """Determine la vue d'une instance depuis ses observations."""
        for obs in obs_list:
            vue = obs.raw_data.get("vue", "")
            if vue:
                return vue
        return f"page_{obs_list[0].page}" if obs_list else "inconnue"

    def _determine_niveau(self, obs_list: list[Observation]) -> str:
        """Determine le niveau depuis les observations."""
        for obs in obs_list:
            niveau = obs.raw_data.get("niveau", "")
            if niveau:
                return niveau
        return "inconnu"

    def cross_check(self, store: EvidenceStore) -> list[dict]:
        """Verification croisee entre instances et tableaux."""
        ecart_list = []

        instance_reperes = {i.repere for i in store.instances}
        table_reperes = store.get_table_reperes()

        # Repere dans les vues mais pas dans les tableaux
        view_only = instance_reperes - table_reperes
        for rep in sorted(view_only):
            ecart_list.append({
                "type": "view_without_table",
                "repere": rep,
                "description": f"Repere '{rep}' detecte dans les vues mais absent des tableaux",
            })

        # Repere dans les tableaux mais pas dans les vues
        table_only = table_reperes - instance_reperes
        for rep in sorted(table_only):
            ecart_list.append({
                "type": "table_without_view",
                "repere": rep,
                "description": f"Repere '{rep}' present dans les tableaux mais non detecte dans les vues",
            })

        return ecart_list
