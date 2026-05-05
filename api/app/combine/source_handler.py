"""
Source handler for loading winner reports from evaluation results.

Queries the evaluation database to find top-scoring reports
and loads their content from the file system.
"""
import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class WinnerReport:
    """A winner report with its metadata."""
    doc_id: str
    content: str
    file_path: str
    score: float
    model: Optional[str] = None
    provider: Optional[str] = None


class SourceHandler:
    """
    Handles loading winner reports from evaluation results.

    Queries the evaluation database to find top-scoring documents,
    then loads their content from the file system.
    """

    def __init__(self, db_path: str, output_folder: str):
        """
        Initialize the source handler.

        Args:
            db_path: Path to the SQLite evaluation database
            output_folder: Directory containing the report files
        """
        self.db_path = db_path
        self.output_folder = output_folder

    def get_top_reports_by_single_doc(self, limit: int = 2) -> list[WinnerReport]:
        """
        Get top reports based on single-doc evaluation scores.

        Args:
            limit: Number of top reports to retrieve

        Returns:
            List of WinnerReport objects
        """
        if not os.path.exists(self.db_path):
            logger.error(f"Database not found at {self.db_path}")
            return []

        top_docs = []

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Check if table exists
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='single_doc_results'"
            )
            if not cursor.fetchone():
                logger.warning("single_doc_results table not found")
                conn.close()
                return []

            # Get top docs by average score
            cursor.execute(
                """
                SELECT doc_id, AVG(score) as avg_score
                FROM single_doc_results
                GROUP BY doc_id
                ORDER BY avg_score DESC
                LIMIT ?
                """,
                (limit,)
            )

            rows = cursor.fetchall()
            conn.close()

            for doc_id, avg_score in rows:
                report = self._load_report(doc_id, avg_score)
                if report:
                    top_docs.append(report)

            logger.info(f"Found {len(top_docs)} top reports by single-doc scores")

        except Exception as e:
            logger.error(f"Error querying single_doc_results: {e}")

        return top_docs

    def get_top_reports_by_elo(self, limit: int = 2) -> list[WinnerReport]:
        """
        Get top reports based on pairwise Elo ratings.

        Args:
            limit: Number of top reports to retrieve

        Returns:
            List of WinnerReport objects
        """
        if not os.path.exists(self.db_path):
            logger.error(f"Database not found at {self.db_path}")
            return []

        top_docs = []

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Check if table exists
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='pairwise_results'"
            )
            if not cursor.fetchone():
                logger.warning("pairwise_results table not found")
                conn.close()
                return []

            # Calculate Elo ratings
            ratings = self._calculate_elo_ratings(cursor)
            conn.close()

            if not ratings:
                return []

            # Sort by rating and take top N
            sorted_ratings = sorted(ratings.items(), key=lambda x: x[1], reverse=True)

            for doc_id, elo_rating in sorted_ratings[:limit]:
                report = self._load_report(doc_id, elo_rating)
                if report:
                    top_docs.append(report)

            logger.info(f"Found {len(top_docs)} top reports by Elo ratings")

        except Exception as e:
            logger.error(f"Error calculating Elo ratings: {e}")

        return top_docs

    def _calculate_elo_ratings(self, cursor: sqlite3.Cursor) -> dict[str, float]:
        """Calculate Elo ratings from pairwise results."""
        ratings: dict[str, float] = {}

        cursor.execute(
            "SELECT doc_id_1, doc_id_2, winner_doc_id FROM pairwise_results"
        )

        for doc1, doc2, winner in cursor.fetchall():
            # Initialize ratings if needed
            ratings.setdefault(doc1, 1000.0)
            ratings.setdefault(doc2, 1000.0)

            r1, r2 = ratings[doc1], ratings[doc2]

            # Expected scores
            e1 = 1.0 / (1.0 + 10.0 ** ((r2 - r1) / 400.0))
            e2 = 1.0 - e1

            # Actual scores
            s1 = 1.0 if winner == doc1 else 0.0
            s2 = 1.0 if winner == doc2 else 0.0

            # Update ratings
            k = 32.0
            ratings[doc1] = r1 + k * (s1 - e1)
            ratings[doc2] = r2 + k * (s2 - e2)

        return ratings

    def _load_report(self, doc_id: str, score: float) -> Optional[WinnerReport]:
        """Load a report file by doc_id."""
        # doc_id is typically the filename
        file_path = os.path.join(self.output_folder, doc_id)

        if not os.path.exists(file_path):
            # Try common variations
            variations = [
                file_path,
                f"{file_path}.md",
                os.path.join(self.output_folder, f"{doc_id}.md"),
            ]

            for variant in variations:
                if os.path.exists(variant):
                    file_path = variant
                    break
            else:
                logger.warning(f"Could not find file for doc_id: {doc_id}")
                return None

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Try to extract model info from filename
            # Format: {name}.{generator}.{iteration}.{model}.{uid}.md
            model = None
            provider = None
            parts = doc_id.replace('.md', '').split('.')
            if len(parts) >= 4:
                model = parts[-2]  # Second to last part is usually model

            return WinnerReport(
                doc_id=doc_id,
                content=content,
                file_path=file_path,
                score=score,
                model=model,
                provider=provider,
            )

        except Exception as e:
            logger.error(f"Error loading report {doc_id}: {e}")
            return None

    def get_top_reports(self, limit: int = 2, prefer_elo: bool = True) -> list[WinnerReport]:
        """
        Get top reports using the explicitly selected method only.

        Args:
            limit: Number of top reports to retrieve
            prefer_elo: When True, use Elo ratings. When False, use single-doc scores.

        Returns:
            List of WinnerReport objects
        """
        if prefer_elo:
            return self.get_top_reports_by_elo(limit)
        return self.get_top_reports_by_single_doc(limit)
