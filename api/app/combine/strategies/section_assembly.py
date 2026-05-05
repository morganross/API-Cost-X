"""
Section assembly strategy - best section from each report.

This strategy splits reports into sections and selects
the best version of each section from the available reports.
"""
import logging
import re
from typing import Optional

from . import CombineStrategy, CombineStrategyType, CombineInput, CombineResult

logger = logging.getLogger(__name__)


class SectionAssemblyStrategy(CombineStrategy):
    """
    Assemble the best sections from multiple reports.

    Splits each report into sections (by markdown headers),
    then selects the best version of each section based on
    length, detail, or other heuristics.
    """

    def __init__(self, prefer_longer: bool = True, min_section_length: int = 50):
        self._prefer_longer = prefer_longer
        self._min_section_length = min_section_length

    @property
    def strategy_type(self) -> CombineStrategyType:
        return CombineStrategyType.SECTION_ASSEMBLY

    @property
    def display_name(self) -> str:
        return "Section Assembly"

    @property
    def requires_llm(self) -> bool:
        return False

    def _split_into_sections(self, content: str) -> dict[str, str]:
        """
        Split markdown content into sections by headers.

        Returns dict mapping normalized header to section content.
        """
        sections = {}

        # Split by markdown headers (## or ###)
        # Pattern matches ## or ### followed by text
        pattern = r'^(#{2,3})\s+(.+)$'

        lines = content.split('\n')
        current_header = "Introduction"
        current_content = []

        for line in lines:
            match = re.match(pattern, line)
            if match:
                # Save previous section
                if current_content:
                    section_text = '\n'.join(current_content).strip()
                    if len(section_text) >= self._min_section_length:
                        sections[self._normalize_header(current_header)] = section_text

                # Start new section
                current_header = match.group(2).strip()
                current_content = [line]
            else:
                current_content.append(line)

        # Don't forget the last section
        if current_content:
            section_text = '\n'.join(current_content).strip()
            if len(section_text) >= self._min_section_length:
                sections[self._normalize_header(current_header)] = section_text

        return sections

    def _normalize_header(self, header: str) -> str:
        """Normalize header for comparison (lowercase, strip punctuation)."""
        # Remove common variations
        normalized = header.lower().strip()
        normalized = re.sub(r'[^\w\s]', '', normalized)
        normalized = re.sub(r'\s+', ' ', normalized)
        return normalized

    def _select_best_section(
        self,
        section_name: str,
        candidates: list[tuple[str, int]]  # (content, report_index)
    ) -> tuple[str, int]:
        """
        Select the best version of a section from candidates.

        Args:
            section_name: Normalized section name
            candidates: List of (content, report_index) tuples

        Returns:
            (best_content, source_report_index)
        """
        if not candidates:
            return ("", -1)

        if len(candidates) == 1:
            return candidates[0]

        if self._prefer_longer:
            # Select the longest version
            return max(candidates, key=lambda x: len(x[0]))
        else:
            # Select the first (highest-scored report's version)
            return candidates[0]

    async def combine(self, input: CombineInput) -> CombineResult:
        """Assemble best sections from all reports."""
        if not input.reports:
            return CombineResult(
                content="",
                strategy_used=self.strategy_type,
                input_report_count=0,
                output_length=0,
            )

        # If only one report, return as-is
        if len(input.reports) == 1:
            return CombineResult(
                content=input.reports[0],
                strategy_used=self.strategy_type,
                input_report_count=1,
                output_length=len(input.reports[0]),
                metadata={"single_report_passthrough": True},
            )

        # Split all reports into sections
        all_sections: list[dict[str, str]] = []
        for report in input.reports:
            all_sections.append(self._split_into_sections(report))

        # Collect all unique section names (preserving order from first report)
        section_order = []
        seen_sections = set()

        for sections in all_sections:
            for section_name in sections.keys():
                if section_name not in seen_sections:
                    section_order.append(section_name)
                    seen_sections.add(section_name)

        # For each section, collect candidates and select best
        assembled_sections = []
        section_sources = {}

        for section_name in section_order:
            candidates = []
            for report_idx, sections in enumerate(all_sections):
                if section_name in sections:
                    candidates.append((sections[section_name], report_idx))

            best_content, source_idx = self._select_best_section(section_name, candidates)
            if best_content:
                assembled_sections.append(best_content)
                section_sources[section_name] = source_idx

        combined = "\n\n".join(assembled_sections)

        return CombineResult(
            content=combined,
            strategy_used=self.strategy_type,
            input_report_count=len(input.reports),
            output_length=len(combined),
            metadata={
                "sections_assembled": len(assembled_sections),
                "section_sources": section_sources,
                "prefer_longer": self._prefer_longer,
            },
        )
