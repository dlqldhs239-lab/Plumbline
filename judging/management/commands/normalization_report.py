"""Print the normalization proof for an event as Markdown.

    python manage.py normalization_report sample-hack-2026 > docs/normalization-proof.md

Shows judge statistics, then every project's raw mean, normalized score,
jury-size adjusted score and rank movement. This is the artifact JUDGING.md refers to; it is regenerated
from the database, never edited by hand.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from events.models import Event, EventRole, Project, Role
from judging.export import md_cell
from judging.normalization import normalize
from judging.services import collect_reviews


class Command(BaseCommand):
    help = "Markdown proof of cross-judge normalization for one event."

    def add_arguments(self, parser):
        parser.add_argument("slug")

    def handle(self, *args, **options):
        try:
            event = Event.objects.get(slug=options["slug"])
        except Event.DoesNotExist:
            raise CommandError("no such event") from None
        reviews, weights, rubric = collect_reviews(event)
        result = normalize(reviews, rubric.scale_min, rubric.scale_max, jury_k=rubric.effective_jury_k())
        titles = {str(p.id): p for p in Project.objects.filter(event=event).select_related("track")}
        judge_label = {}
        for r in EventRole.objects.filter(event=event, role=Role.JUDGE).select_related("user"):
            judge_label[str(r.user_id)] = f"{r.external_id or r.user.username}"

        out = self.stdout.write
        out(f"# Normalization proof — {event.name}")
        out("")
        out(
            f"Method `{result.method}`; rubric scale {rubric.scale_min}–{rubric.scale_max}; weights "
            + ", ".join(f"{k}={v:g}" for k, v in weights.items())
            + "."
        )
        out(
            f"{len(reviews)} submitted reviews over {len(result.projects)} eligible projects by {len(result.judges)} judges. "
            f"Panel mean {result.panel_mean:.3f}, panel spread {result.panel_stdev:.3f}. "
            f"Judge shrinkage K = {result.shrink_k:g}; jury-size adjustment J = {result.jury_k:g}. "
            f"Solved in {result.rounds} rounds{'' if result.settled else ', and NOT settled'}."
        )
        out("")
        out("## Judges")
        out("")
        out(
            "Leniency is what is taken off each of the judge's scores. It is measured against what other "
            "judges gave the same projects, and believed in the proportion reviews / (reviews + K)."
        )
        out("")
        out("| judge | reviews | mean | from the panel | spread | believed | leniency | note |")
        out("|---|---:|---:|---:|---:|---:|---:|---|")
        for jid, js in sorted(result.judges.items(), key=lambda kv: judge_label.get(kv[0], kv[0])):
            note = (
                "every score the same: set aside, says nothing of any project"
                if js.flat
                else ("few reviews: leniency believed little" if js.n < 3 else "")
            )
            taken = "" if js.flat else f"{js.leniency:+.3f}"
            out(
                f"| {judge_label.get(jid, jid)} | {js.n} | {js.mean:.3f} | {js.mean - result.panel_mean:+.3f} | "
                f"{js.stdev:.3f} | {js.shrink_weight:.2f} | {taken} | {note} |"
            )
        out("")
        out("## Projects")
        out("")
        out(
            "Sorted by final rank. Δ is raw rank minus final rank: positive means the project moved up "
            "once judge bias was removed and the number of reviews was accounted for."
        )
        out("")
        out(
            "| rank | norm. rank | raw rank | Δ | project | track | reviews | that count | raw mean | normalized | weight | adjusted | judges |"
        )
        out("|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---|")
        by_project_judges: dict[str, list[str]] = {}
        for r in reviews:
            by_project_judges.setdefault(r.project_id, []).append(judge_label.get(r.judge_id, r.judge_id))
        for pid, s in sorted(result.projects.items(), key=lambda kv: (kv[1].rank or 0, kv[0])):
            p = titles.get(pid)
            delta = (s.rank_raw or 0) - (s.rank or 0)
            sign = f"+{delta}" if delta > 0 else str(delta)
            out(
                f"| {s.rank} | {s.rank_normalized} | {s.rank_raw} | {sign} | {md_cell(p.title if p else pid)} | "
                f"{md_cell(p.track.name if p and p.track else '')} | {s.n} | {s.informative} | {s.raw_mean:.3f} | "
                f"{s.normalized:.3f} | "
                f"{s.jury_weight:.2f} | {s.adjusted:.3f} | {', '.join(sorted(by_project_judges.get(pid, [])))} |"
            )
        moved = sum(1 for s in result.projects.values() if s.rank_raw != s.rank)
        biggest = max(result.projects.values(), key=lambda s: abs((s.rank_raw or 0) - (s.rank or 0)))
        out("")
        out(
            f"{moved} of {len(result.projects)} projects changed rank. Largest move: "
            f"{titles[biggest.project_id].title if biggest.project_id in titles else biggest.project_id} "
            f"from raw #{biggest.rank_raw} to final #{biggest.rank}."
        )
