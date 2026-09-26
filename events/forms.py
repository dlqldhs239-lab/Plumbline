from django import forms
from django.utils import timezone
from django.utils.text import slugify

from .models import CustomQuestion, Event, Project, Team, Track


class DateTimeLocalInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, **kwargs):
        kwargs.setdefault("format", "%Y-%m-%dT%H:%M")
        super().__init__(**kwargs)


class EventForm(forms.ModelForm):
    tracks_text = forms.CharField(
        label="Tracks",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "One track per line"}),
        help_text="One per line. Existing tracks are kept; new lines are added.",
    )

    class Meta:
        model = Event
        fields = [
            "name", "slug", "tagline", "description",
            "submissions_open_at", "submissions_close_at", "judging_open_at", "judging_close_at",
            "reviews_per_project", "voting_access", "voting_open_at", "voting_close_at", "voting_credits",
            "comments_enabled", "is_listed",
        ]
        widgets = {
            "submissions_open_at": DateTimeLocalInput(),
            "submissions_close_at": DateTimeLocalInput(),
            "judging_open_at": DateTimeLocalInput(),
            "judging_close_at": DateTimeLocalInput(),
            "voting_open_at": DateTimeLocalInput(),
            "voting_close_at": DateTimeLocalInput(),
            "description": forms.Textarea(attrs={"rows": 6}),
        }
        help_texts = {
            "slug": "Used in URLs. Leave blank to derive from the name.",
            "submissions_close_at": "All times are UTC. Submissions are refused the moment this passes.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False
        for name in ("submissions_open_at", "submissions_close_at", "judging_open_at", "judging_close_at", "voting_open_at", "voting_close_at"):
            self.fields[name].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"]
        if self.instance.pk:
            self.fields["tracks_text"].initial = "\n".join(self.instance.tracks.values_list("name", flat=True))

    def clean(self):
        data = super().clean()
        if not data.get("slug"):
            data["slug"] = slugify(data.get("name", ""))
        if data.get("submissions_open_at") and data.get("submissions_close_at"):
            if data["submissions_close_at"] <= data["submissions_open_at"]:
                self.add_error("submissions_close_at", "Must be after the opening time.")
        if Event.objects.filter(slug=data.get("slug")).exclude(pk=self.instance.pk).exists():
            self.add_error("slug", "That slug is already used.")
        return data

    def save(self, commit=True):
        event = super().save(commit)
        if commit:
            existing = {t.name.lower() for t in event.tracks.all()}
            order = event.tracks.count()
            for line in (self.cleaned_data.get("tracks_text") or "").splitlines():
                name = line.strip()
                if name and name.lower() not in existing:
                    Track.objects.create(event=event, name=name, order=order)
                    existing.add(name.lower())
                    order += 1
        return event


class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ["name"]


class ProjectForm(forms.ModelForm):
    image_urls_text = forms.CharField(
        label="Image gallery URLs", required=False, widget=forms.Textarea(attrs={"rows": 3, "placeholder": "One URL per line"})
    )
    tech_tags_text = forms.CharField(label="Tech tags", required=False, help_text="Comma separated, e.g. django, postgres, htmx")

    class Meta:
        model = Project
        fields = ["title", "tagline", "description", "track", "thumbnail_url", "demo_video_url", "repo_url", "live_url"]
        widgets = {"description": forms.Textarea(attrs={"rows": 10})}

    def __init__(self, *args, event: Event, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.fields["track"].queryset = event.tracks.all()
        self.fields["track"].required = event.tracks.exists()
        if self.instance.pk:
            self.fields["image_urls_text"].initial = "\n".join(self.instance.image_urls or [])
            self.fields["tech_tags_text"].initial = ", ".join(self.instance.tech_tags or [])
        for q in event.custom_questions.all():
            self.fields[f"q_{q.id}"] = self._question_field(q)
            if self.instance.pk:
                ans = self.instance.answers.filter(question=q).first()
                if ans:
                    self.fields[f"q_{q.id}"].initial = ans.value

    @staticmethod
    def _question_field(q: CustomQuestion):
        common = {"label": q.prompt, "required": q.required, "help_text": q.help_text}
        if q.kind == CustomQuestion.Kind.TEXTAREA:
            return forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), **common)
        if q.kind == CustomQuestion.Kind.URL:
            return forms.URLField(**common)
        if q.kind == CustomQuestion.Kind.CHOICE:
            return forms.ChoiceField(choices=[(c, c) for c in q.choices], **common)
        if q.kind == CustomQuestion.Kind.CHECKBOX:
            return forms.BooleanField(**{**common, "required": False})
        return forms.CharField(**common)

    def data_dict(self) -> dict:
        d = {k: self.cleaned_data[k] for k in ("title", "tagline", "description", "track", "thumbnail_url", "demo_video_url", "repo_url", "live_url")}
        d["image_urls"] = self.cleaned_data.get("image_urls_text", "")
        d["tech_tags"] = self.cleaned_data.get("tech_tags_text", "")
        return d

    def answers(self) -> dict[int, str]:
        return {q.id: str(self.cleaned_data.get(f"q_{q.id}", "") or "") for q in self.event.custom_questions.all()}


class JudgeInviteForm(forms.Form):
    email = forms.EmailField(help_text="An account is created if none exists; the judge sets a password via the login page.")
    name = forms.CharField(required=False)
    tracks = forms.ModelMultipleChoiceField(queryset=Track.objects.none(), required=False, help_text="Leave empty to allow every track.")

    def __init__(self, *args, event: Event, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tracks"].queryset = event.tracks.all()


class AssignForm(forms.Form):
    reviews_per_project = forms.IntegerField(min_value=1, max_value=20, initial=3)
    batch = forms.CharField(required=False, help_text="Label for this run, e.g. batch-1")
    seed = forms.IntegerField(required=False, help_text="Optional. Same seed, same assignment.")


class CriterionForm(forms.Form):
    key = forms.SlugField(max_length=40)
    name = forms.CharField(max_length=120)
    weight = forms.DecimalField(max_digits=6, decimal_places=2, min_value=0, initial=1)
    description = forms.CharField(required=False)


CriterionFormSet = forms.formset_factory(CriterionForm, extra=1, can_delete=True)
