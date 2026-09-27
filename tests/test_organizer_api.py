"""API-first: the organizer console's actions through the API, and the same
refusals for everyone who is not an organizer of the event."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.utils import timezone

from accounts.models import ApiToken
from events.models import EventRole, Project, Role
from judging import services as judging_services
from judging.models import JudgeAssignment

from .base import SeededTestCase


class OrganizerApiTests(SeededTestCase):
    def url(self, tail: str) -> str:
        return f"/api/events/{self.event.slug}{tail}"

    def post(self, tail, who, data=None):
        return self.client.post(self.url(tail), data=data or {}, content_type="application/json", **self.bearer(who))

    def test_every_organizer_endpoint_refuses_judges_and_participants(self):
        a = JudgeAssignment.objects.filter(event=self.event).first()
        calls = [
            ("patch", "", {"tagline": "x"}),
            ("get", "/judges", None),
            ("post", "/judges", {"email": "new@example.org"}),
            ("delete", "/judges/jdg_01", None),
            ("put", "/rubric", [{"key": "a", "name": "A"}]),
            ("post", "/projects/1/hide", None),
            ("post", "/assignments", {"judge": "jdg_01", "project_id": 1}),
            ("delete", f"/assignments/{a.pk}", None),
            ("get", "/voters", None),
            ("post", "/voters/links", {"emails": ["a@example.org"]}),
            ("get", "/audit", None),
            ("get", "/progress", None),
            ("post", "/results/recompute", None),
            ("post", "/results/publish", None),
            ("post", "/assignments/auto", {}),
        ]
        for who in ("judge_a", "participant"):
            for method, tail, body in calls:
                kwargs = {"content_type": "application/json", **self.bearer(who)}
                if body is not None:
                    kwargs["data"] = body
                r = getattr(self.client, method)(self.url(tail), **kwargs)
                self.assertEqual(r.status_code, 403, f"{who} {method.upper()} {tail} -> {r.status_code}")
        self.assertFalse(self.event.results_published)
        self.assertEqual(Project.objects.filter(event=self.event, is_hidden=True).count(), 0)

    def test_update_event_is_audited_field_by_field(self):
        new_close = timezone.now() + timedelta(days=3)
        r = self.client.patch(
            self.url(""),
            data={"submissions_close_at": new_close.isoformat(), "tagline": "Extended"},
            content_type="application/json",
            **self.bearer("organizer"),
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["phase"], "submissions")
        entry = self.event.audit_entries.filter(action="event.update").first()
        self.assertEqual(set(entry.detail["changed"]), {"submissions_close_at", "tagline"})
        self.assertEqual(entry.detail["changed"]["tagline"][1], "Extended")
        # With the deadline moved, the participant's late probe is now accepted as a draft.
        r = self.post("/projects", "participant", {"title": "Second wind"})
        self.assertEqual(r.status_code, 201, r.content)

    def test_update_event_rejects_inverted_dates(self):
        r = self.client.patch(
            self.url(""),
            data={"submissions_close_at": (self.event.submissions_open_at - timedelta(days=1)).isoformat()},
            content_type="application/json",
            **self.bearer("organizer"),
        )
        self.assertEqual(r.status_code, 400)

    def test_judges_add_update_remove(self):
        track = self.event.tracks.first()
        r = self.post(
            "/judges", "organizer", {"email": "New.Judge@Example.org", "name": "New Judge", "tracks": [track.id]}
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["email"], "new.judge@example.org")
        self.assertEqual(r.json()["tracks"], [track.name])
        user = User.objects.get(email="new.judge@example.org")
        self.assertFalse(user.has_usable_password())
        r = self.post("/judges", "organizer", {"email": "new.judge@example.org", "tracks": []})
        self.assertEqual(r.json()["tracks"], [])
        self.assertEqual(EventRole.objects.filter(event=self.event, user=user, role=Role.JUDGE).count(), 1)
        r = self.client.delete(self.url(f"/judges/{user.username}"), **self.bearer("organizer"))
        self.assertEqual(r.status_code, 204)
        self.assertFalse(EventRole.objects.filter(event=self.event, user=user).exists())
        # A judge with submitted reviews cannot be removed.
        r = self.client.delete(self.url("/judges/jdg_02"), **self.bearer("organizer"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(self.client.get(self.url("/judges"), **self.bearer("organizer")).json()), 30)

    def test_rubric_is_public_and_locked_once_scored(self):
        r = self.client.get(self.url("/rubric"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([c["key"] for c in r.json()["criteria"]], ["functionality", "quality", "innovation"])
        r = self.client.put(
            self.url("/rubric"),
            data=[{"key": "a", "name": "A", "weight": 2}],
            content_type="application/json",
            **self.bearer("organizer"),
        )
        self.assertEqual(r.status_code, 400)  # the fixture event already has scores

    def test_rubric_replace_on_a_fresh_event(self):
        now = timezone.now()
        r = self.client.post(
            "/api/events",
            data={
                "name": "Fresh",
                "submissions_open_at": now.isoformat(),
                "submissions_close_at": (now + timedelta(days=1)).isoformat(),
            },
            content_type="application/json",
            **self.bearer("organizer"),
        )
        slug = r.json()["slug"]
        r = self.client.put(
            f"/api/events/{slug}/rubric",
            data=[{"key": "impact", "name": "Impact", "weight": 3}, {"key": "craft", "name": "Craft", "weight": 1}],
            content_type="application/json",
            **self.bearer("organizer"),
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([(c["key"], c["weight"]) for c in r.json()["criteria"]], [("impact", 3.0), ("craft", 1.0)])

    def test_hide_project_removes_it_from_the_gallery(self):
        project = Project.objects.get(external_id="prj_01")
        r = self.post(f"/projects/{project.pk}/hide", "organizer")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(self.client.get(f"/events/{self.event.slug}/gallery/"), "Glass Signal")
        titles = [p["title"] for p in self.client.get(self.url("/projects")).json()]
        self.assertNotIn("Glass Signal", titles)
        self.client.post(self.url(f"/projects/{project.pk}/hide?hidden=false"), **self.bearer("organizer"))
        self.assertContains(self.client.get(f"/events/{self.event.slug}/gallery/"), "Glass Signal")

    def test_gallery_excludes_the_flagged_duplicate(self):
        titles = [p["title"] for p in self.client.get(self.url("/projects")).json()]
        self.assertEqual(len(titles), 40)
        self.assertEqual(titles.count("Dry Harbour"), 1)

    def test_manual_assignment_and_removal(self):
        role = EventRole.objects.get(event=self.event, external_id="jdg_01")
        judge = role.user
        track_ids = list(role.tracks.values_list("id", flat=True))
        open_to_judge = judging_services.eligible_projects(self.event).exclude(assignments__judge=judge)
        if track_ids:
            open_to_judge = open_to_judge.filter(track_id__in=track_ids)
        project = open_to_judge.first()
        r = self.post("/assignments", "organizer", {"judge": "jdg_01", "project_id": project.pk, "batch": "extra"})
        self.assertEqual(r.status_code, 201, r.content)
        aid = r.json()["id"]
        self.assertEqual(r.json()["status"], "pending")
        self.assertTrue(JudgeAssignment.objects.filter(pk=aid, judge=judge).exists())
        self.assertEqual(
            self.client.delete(self.url(f"/assignments/{aid}"), **self.bearer("organizer")).status_code, 204
        )
        submitted = JudgeAssignment.objects.filter(event=self.event, status="submitted").first()
        r = self.client.delete(self.url(f"/assignments/{submitted.pk}"), **self.bearer("organizer"))
        self.assertEqual(r.status_code, 400)

    def test_manual_assignment_refuses_own_team(self):
        project = Project.objects.get(external_id="prj_01")
        member = project.team.memberships.first().user
        EventRole.objects.create(event=self.event, user=member, role=Role.JUDGE)
        r = self.post("/assignments", "organizer", {"judge": member.username, "project_id": project.pk})
        self.assertEqual(r.status_code, 400)

    def test_audit_json_pages_backwards(self):
        self.post("/results/recompute", "organizer")
        r = self.client.get(self.url("/audit?limit=1"), **self.bearer("organizer"))
        self.assertEqual(r.status_code, 200)
        first = r.json()
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["action"], "results.recompute")
        r = self.client.get(self.url(f"/audit?limit=5&before_id={first[0]['id']}"), **self.bearer("organizer"))
        self.assertTrue(all(e["id"] < first[0]["id"] for e in r.json()))

    def test_organizer_of_another_event_is_refused_here(self):
        other = User.objects.create_user("elsewhere", "elsewhere@example.org", "pw")
        _, raw = ApiToken.issue(other)
        now = timezone.now()
        self.client.post(
            "/api/events",
            data={
                "name": "Elsewhere",
                "submissions_open_at": now.isoformat(),
                "submissions_close_at": (now + timedelta(days=1)).isoformat(),
            },
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {raw}",
        )
        for tail in ("/judges", "/audit", "/voters", "/export/scores.csv", "/progress"):
            r = self.client.get(self.url(tail), HTTP_AUTHORIZATION=f"Bearer {raw}")
            self.assertEqual(r.status_code, 403, tail)
