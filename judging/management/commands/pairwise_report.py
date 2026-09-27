"""Print the pairwise proof for an event as Markdown.

    python manage.py pairwise_report sample-hack-2026 > docs/pairwise-proof.md

If judges have given comparisons, those are used. If they have not (the
fixture set has none), the comparisons implied by the rubric scores are
used instead: within one judge, of every two projects they scored, the one
with the higher score is preferred. Either way the ranking is set beside
the normalized rubric ranking, so the two methods can be held against each
other on the same data.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from events.models import Event, EventRole, Project, Role
from judging.normalization import normalize
from judging.pairwise import estimate, from_scores, kendall_tau
from judging.pairwise_services import collect
from judging.services import collect_reviews, eligible_projects


class Command(BaseCommand):
    help = "Markdown proof of the Bradley-Terry ranking for one event."

    def add_arguments(self, parser):
        parser.add_argument("slug")

    def handle(self, *args, **options):
        try:
            event = Event.objects.get(slug=options["slug"])
        except Event.DoesNotExist:
            raise CommandError("no such event") from None
        reviews, _, rubric = collect_reviews(event)
        rubric_result = normalize(reviews, rubric.scale_min, rubric.scale_max, jury_k=rubric.effective_jury_k())
        given = collect(event)
        implied = from_scores([(r.judge_id, r.project_id, r.score) for r in reviews])
        comparisons, source = (given, "given by judges") if given else (implied, "implied by the rubric scores")
        ids = [str(i) for i in eligible_projects(event).order_by("id").values_list("id", flat=True)]
        result = estimate(comparisons, ids)
        titles = {str(p.id): p for p in Project.objects.filter(event=event).select_related("track")}
        labels = {
            str(r.user_id): (r.external_id or r.user.username)
            for r in EventRole.objects.filter(event=event, role=Role.JUDGE).select_related("user")
        }
        out = self.stdout.write
        out(f"# Pairwise proof: {event.name}")
        out("")
        out(
            f"Method `{result.method}`, prior {result.prior:g}. {result.comparisons} comparisons, {source}, "
            f"over {len(ids)} eligible projects. "
            f"{sum(1 for c in comparisons if c.tie)} of them are ties."
        )
        out(
            f"The algorithm {'settled' if result.converged else 'did not settle'} after {result.iterations} "
            f"iterations; log likelihood {result.log_likelihood:.3f}."
        )
        if result.components > 1:
            out(
                f"The comparisons fall into **{result.components} groups** that were never compared with each "
                "other. The order inside a group rests on comparisons; the order between groups rests on the prior."
            )
        if result.unheard:
            out(f"{len(result.unheard)} projects took part in no comparison and are not ranked.")
        scores_a = {i: s.score for i, s in result.projects.items() if s.comparisons}
        scores_b = {i: s.adjusted for i, s in rubric_result.projects.items() if s.adjusted is not None}
        scores_raw = {i: s.raw_mean for i, s in rubric_result.projects.items() if s.raw_mean is not None}
        tau, tau_raw = kendall_tau(scores_a, scores_b), kendall_tau(scores_a, scores_raw)
        out("")
        out("## Agreement with the rubric ranking")
        out("")
        out("| compared with | Kendall tau |")
        out("|---|---:|")
        out(f"| adjusted rubric score | {tau:.3f} |" if tau is not None else "| adjusted rubric score | n/a |")
        out(f"| raw rubric mean | {tau_raw:.3f} |" if tau_raw is not None else "| raw rubric mean | n/a |")
        out("")
        out("## Projects")
        out("")
        out("Score is the log strength: 0 is an average project, and a difference of 1 means odds of about 2.7 to 1.")
        out("")
        out("| pairwise place | rubric place | project | track | compared | preferred | score | strength |")
        out("|---:|---:|---|---|---:|---:|---:|---:|")
        ordered = sorted(result.projects.values(), key=lambda s: (s.rank is None, s.rank or 0, s.project_id))
        for s in ordered:
            p = titles.get(s.project_id)
            rub = rubric_result.projects.get(s.project_id)
            out(
                f"| {s.rank if s.rank else 'n/a'} | {rub.rank if rub else 'n/a'} | {p.title if p else s.project_id} | "
                f"{p.track.name if p and p.track else ''} | {s.comparisons} | {s.wins:g} | {s.score:+.3f} | "
                f"{s.strength:.3f} |"
            )
        by_judge: dict[str, int] = {}
        for c in comparisons:
            by_judge[c.judge] = by_judge.get(c.judge, 0) + 1
        out("")
        out("## Comparisons per judge")
        out("")
        out("| judge | comparisons |")
        out("|---|---:|")
        for j in sorted(by_judge, key=lambda k: labels.get(k, k)):
            out(f"| {labels.get(j, j)} | {by_judge[j]} |")
