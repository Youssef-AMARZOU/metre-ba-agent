"""
Runner — pipeline principal qui orchestre tous les modules.
Chaque module est appele en sequence, avec l'EvidenceStore comme interface.
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any, Optional

from .evidence_store import EvidenceStore
from .format_detector import FormatDetector, PlanFormat
from .zone_segmenter import ZoneSegmenter
from .legend_learner import LegendLearner
from .table_learner import TableLearner
from .grid_learner import GridLearner
from .element_scanner import ElementScanner
from .entity_resolver import EntityResolver
from .quantity_calculator import QuantityCalculator
from .reconciler import Reconciler
from .validation_gate import ValidationGate


class PipelineRunner:
    """Pipeline complet d'extraction de metre BA."""

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.store = EvidenceStore()
        self.format_detector = FormatDetector()
        self.zone_segmenter = ZoneSegmenter()
        self.legend_learner = LegendLearner()
        self.table_learner = TableLearner()
        self.grid_learner = GridLearner()
        self.element_scanner = ElementScanner()
        self.entity_resolver = EntityResolver()
        self.quantity_calculator = QuantityCalculator()
        self.reconciler = Reconciler()
        self.validation_gate = ValidationGate()

    def run(self, pdf_path: str) -> dict:
        """Execute le pipeline complet sur un fichier PDF."""
        start_time = time.time()

        if self.verbose:
            print(f"\n{'='*60}")
            print(f"PIPELINE ZERO-MISS — {Path(pdf_path).name}")
            print(f"{'='*60}")

        # Phase 0: Detection du format
        self._log("[1/9] Detection du format...")
        fmt = self.format_detector.detect(pdf_path)
        self.store.metadata["format"] = fmt.value
        if self.verbose:
            print(f"  Format: {fmt.value}")

        if fmt in (PlanFormat.DXF, PlanFormat.IFC):
            return self._run_cad_pipeline(pdf_path, fmt)

        # Phase 1: Ouverture du document
        import pymupdf
        doc = pymupdf.open(pdf_path)
        self.store.metadata["pages"] = doc.page_count
        if self.verbose:
            print(f"  Pages: {doc.page_count}")

        # Phase 2: Segmentation en zones
        self._log("[2/9] Segmentation en zones...")
        zones = self.zone_segmenter.segment_document(doc)
        if self.verbose:
            zone_types = {}
            for z in zones:
                zone_types[z.zone_type] = zone_types.get(z.zone_type, 0) + 1
            print(f"  Zones: {zone_types}")

        # Phase 3: Apprentissage de la legende
        self._log("[3/9] Apprentissage de la legende...")
        legend = self.legend_learner.learn_from_zones(zones)

        # Aussi chercher dans le texte brut de chaque page
        for page_num in range(doc.page_count):
            page = doc[page_num]
            try:
                text = page.get_text("text")
                page_legend = self.legend_learner.learn_from_text(text, page_num + 1)
                for prefix, evidence in page_legend.items():
                    if prefix not in legend:
                        legend[prefix] = evidence
            except Exception:
                pass

        # Ajouter le fallback si la legende est incomplete
        legend_with_fallback = self.legend_learner.get_with_fallback(legend)
        self.store.set_legend(legend_with_fallback)
        if self.verbose:
            plan_legend = {k: v for k, v in legend_with_fallback.items()
                          if v.source_type.value == "lu"}
            print(f"  Legende: {len(plan_legend)} entrees du plan, "
                  f"{len(legend_with_fallback)} total (avec fallback)")

        # Phase 4: Apprentissage des tableaux
        self._log("[4/9] Apprentissage des tableaux...")
        for page_num in range(doc.page_count):
            page = doc[page_num]
            raw_tables = self.table_learner.detect_tables_from_pymupdf(page)
            for raw_table in raw_tables:
                table_data = self.table_learner.learn_table(raw_table)
                if table_data:
                    # Nommer le tableau selon son contenu
                    first_key = list(table_data.keys())[0] if table_data else ""
                    table_name = f"table_page{page_num+1}_{first_key[:10]}"
                    self.store.add_table(table_name, table_data)
                    if self.verbose:
                        print(f"  Tableau {table_name}: {len(table_data)} lignes "
                              f"(reperes: {list(table_data.keys())[:5]})")

        # Enrichir la legende depuis les tableaux
        for table_data in self.store.tables.values():
            table_legend = self.legend_learner.learn_from_table(table_data)
            for prefix, evidence in table_legend.items():
                if prefix not in self.store.legend or \
                   self.store.legend[prefix].source_type.value == "fallback":
                    self.store.legend[prefix] = evidence

        # Phase 5: Apprentissage des grilles
        self._log("[5/9] Apprentissage des grilles d'axes...")
        grids = self.grid_learner.learn_grids_per_view(doc)
        for vue, grid in grids.items():
            self.store.set_grid(vue, grid)
        if self.verbose:
            print(f"  Grilles: {list(grids.keys())}")

        # Phase 6: Scan des elements
        self._log("[6/9] Scan des elements structuraux...")
        # Construire la regex dynamique depuis la legende
        dynamic_regex = self.legend_learner.build_dynamic_regex(self.store.legend)
        self.element_scanner.legend_regex = dynamic_regex

        # Prefixes sans numero (ex: BN, CH dans "BN-(25X30)")
        bare_prefixes = [
            k for k in self.store.legend.keys()
            if len(k) >= 2 and k.isalpha()
        ]
        self.element_scanner._bare_prefixes = bare_prefixes

        observations = self.element_scanner.scan_document(doc)
        for obs in observations:
            self.store.add_observation(obs)
        if self.verbose:
            print(f"  Observations: {len(observations)}")

        # Phase 7: Resolution des entites
        self._log("[7/9] Resolution des entites...")
        instances = self.entity_resolver.resolve(self.store)
        for inst in instances:
            self.store.add_instance(inst)
        if self.verbose:
            print(f"  Instances: {len(instances)}")

        # Phase 8: Calcul des quantites
        self._log("[8/9] Calcul des quantites...")
        metre_lines = self.quantity_calculator.calculate_all(self.store)
        for line in metre_lines:
            self.store.add_metre_line(line)
        if self.verbose:
            complete = sum(1 for l in metre_lines if l.statut == "complet")
            print(f"  Lignes metre: {len(metre_lines)} ({complete} completes)")

        # Phase 9: Reconciliation et validation
        self._log("[9/9] Reconciliation et validation...")
        reconciliation = self.reconciler.reconcile(self.store)
        validation = self.validation_gate.validate(reconciliation)

        self.store.ecarts = reconciliation["ecarts"]
        self.store.warnings = reconciliation["avertissements"]

        elapsed = time.time() - start_time
        if self.verbose:
            print(f"\n  Etat: {validation['etat'].upper()}")
            print(f"  Resume: {validation['resume']}")
            print(f"  Temps: {elapsed:.1f}s")

        # Construire le resultat final
        return self._build_result(reconciliation, validation, elapsed)

    def _run_cad_pipeline(self, file_path: str, fmt: PlanFormat) -> dict:
        """Pipeline pour DXF/IFC."""
        if fmt == PlanFormat.DXF:
            try:
                from core.dxf_extractor import DXFExtractor
                extractor = DXFExtractor()
                elements = extractor.extract(file_path)
                return {"format": "dxf", "elements": elements}
            except Exception as e:
                return {"format": "dxf", "error": str(e)}

        elif fmt == PlanFormat.IFC:
            try:
                from core.ifc_extractor import extract_from_ifc
                result = extract_from_ifc(file_path)
                return {"format": "ifc", "elements": result}
            except Exception as e:
                return {"format": "ifc", "error": str(e)}

        return {"format": fmt.value, "error": "Format non supporte"}

    def _build_result(self, reconciliation: dict,
                       validation: dict, elapsed: float) -> dict:
        """Construit le resultat final du pipeline."""
        return {
            "metre": [m.to_dict() for m in self.store.metre_lines],
            "instances": [i.to_dict() for i in self.store.instances],
            "observations_count": len(self.store.observations),
            "legend": {
                k: {
                    "value": v.value,
                    "source": v.source_type.value,
                    "confidence": v.confidence,
                }
                for k, v in self.store.legend.items()
            },
            "tables": {
                name: list(data.keys())
                for name, data in self.store.tables.items()
            },
            "grids": self.store.grids,
            "rapport": {
                "statistiques": reconciliation["statistiques"],
                "ecarts": reconciliation["ecarts"],
                "avertissements": reconciliation["avertissements"],
                "couverture": reconciliation["couverture"],
            },
            "validation": validation,
            "elapsed_seconds": round(elapsed, 2),
        }

    def _log(self, msg: str):
        if self.verbose:
            print(f"\n{msg}")
