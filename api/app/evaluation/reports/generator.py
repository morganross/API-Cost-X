import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Union
from jinja2 import Environment, FileSystemLoader

from app.infra.db.models.run import Run
from .timeline import generate_timeline_chart

logger = logging.getLogger(__name__)

class ReportGenerator:
    """
    Generates reports for APICostX runs.
    """

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.template_dir = Path(__file__).parent / "templates"
        self.env = Environment(loader=FileSystemLoader(self.template_dir))

    def generate_timeline_json(self, run: Union[Run, Dict[str, Any]], run_data: Dict[str, Any]) -> Path:
        """
        Generate the eval_timeline_chart.json artifact.
        """
        chart = generate_timeline_chart(run, run_data)

        # Ensure output dir exists
        run_id = run.get("id") if isinstance(run, dict) else run.id
        self.output_dir.mkdir(parents=True, exist_ok=True)

        output_path = self.output_dir / "eval_timeline_chart.json"

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(chart.to_dict(), f, indent=2)

        logger.info(f"Generated timeline chart for run {run_id} at {output_path}")
        return output_path

    def generate_html_report(self, run: Union[Run, Dict[str, Any]], run_data: Dict[str, Any]) -> Path:
        """
        Generate the unified HTML report.
        """
        # Generate timeline data first
        chart = generate_timeline_chart(run, run_data)
        self.generate_timeline_json(run, run_data)

        run_id = run.get("id") if isinstance(run, dict) else run.id

        # Render template
        template = self.env.get_template("report.html")
        html_content = template.render(
            run=run if isinstance(run, dict) else run.__dict__,
            timeline=chart
        )

        self.output_dir.mkdir(parents=True, exist_ok=True)
        html_path = self.output_dir / "report.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        logger.info(f"Generated HTML report for run {run_id} at {html_path}")
        return html_path
