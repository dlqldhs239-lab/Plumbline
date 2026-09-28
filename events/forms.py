from django import forms
from django.core.exceptions import ValidationError

from . import services, theme
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
    theme_preset = forms.ChoiceField(
        label="Colour theme",
        required=False,
        choices=[("", "Keep the four colours below")] + [(k, v[0]) for k, v in theme.PRESETS.items()],
        help_text="Pick a preset, or set the four colours yourself. Unreadable combinations are refused.",
    )

    class Meta:
        model = Event
        fields = [
            "name",
            "slug",
            "tagline",
            "description",
            "submissions_open_at",
            "submissions_close_at",
            "judging_open_at",
            "judging_close_at",
            "reviews_per_project",
            "voting_access",
            "voting_open_at",
            "voting_close_at",
            "voting_credits",
            "comments_enabled",
            "is_listed",
            "theme_ground",
            "theme_ink",
            "theme_accent",
            "theme_signal",
        ]
        widgets = {
            "theme_ground": forms.TextInput(attrs={"type": "color"}),
            "theme_ink": forms.TextInput(attrs={"type": "color"}),
            "theme_accent": forms.TextInput(attrs={"type": "color"}),
            "theme_signal": forms.TextInput(attrs={"type": "color"}),
            "submissions_open_at": DateTimeLocalInput(),
            "submissions_close_at": DateTimeLocalInput(),
            "judging_open_at": DateTimeLocalInput(),
            "judging_close_at": DateTimeLocalInput(),
            "voting_open_at": DateTimeLocalInput(),
            "voting_close_at": DateTimeLocalInput(),
            "description": forms.Textarea(attrs={"rows": 6}),
        }
        labels = {
            "slug": "Address",
            "submissions_open_at": "Open",
            "submissions_close_at": "Close, the deadline",
            "judging_open_at": "Judging opens",
            "judging_close_at": "Judging closes",
            "reviews_per_project": "Reviews each project should get",
            "voting_access": "Who may vote",
            "voting_open_at": "Voting opens",
            "voting_close_at": "Voting closes",
            "voting_credits": "Credits per voter",
            "comments_enabled": "Let signed-in people comment on projects",
            "is_listed": "Show this event on the home page",
        }
        help_texts = {
            "slug": "The part of the address after /events/. Leave blank to derive it from the name.",
            "submissions_close_at": "Submissions are refused the moment this passes.",
            "reviews_per_project": "Automatic assignment aims for this many; the jury-size adjustment follows it.",
            "voting_credits": "For quadratic voting: putting n votes on one project costs n squared. 0 means one vote per project.",
            "is_listed": "",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False
        for name in (
            "submissions_open_at",
            "submissions_close_at",
            "judging_open_at",
            "judging_close_at",
            "voting_open_at",
            "voting_close_at",
        ):
            self.fields[name].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"]
        if self.instance.pk:
            self.fields["tracks_text"].initial = "\n".join(self.instance.tracks.values_list("name", flat=True))

    def clean(self):
        data = super().clean()
        if data.get("theme_preset"):
            data.update(theme.preset(data["theme_preset"]))
            for field, value in theme.preset(data["theme_preset"]).items():
                setattr(self.instance, field, value)
        if self.instance.pk and not data.get("slug"):
            data["slug"] = self.instance.slug
        try:
            data["slug"] = services.clean_slug(data.get("slug"), data.get("name", ""), exclude_pk=self.instance.pk)
        except ValidationError as e:
            self.add_error("slug", e)
        if data.get("submissions_open_at") and data.get("submissions_close_at"):
            if data["submissions_close_at"] <= data["submissions_open_at"]:
                self.add_error("submissions_close_at", "Must be after the opening time.")
        for name in self.track_names():
            if len(name) > 120:
                self.add_error("tracks_text", "A track name can be at most 120 characters.")
                break
        return data

    def track_names(self) -> list[str]:
        text = (self.cleaned_data or {}).get("tracks_text") or ""
        return [line.strip() for line in text.splitlines() if line.strip()]

    def event_data(self) -> dict:
        """The settings as the service layer takes them. Saving goes through
        events.services so the rules and the audit entry are the same as for
        the API."""
        data = {f: self.cleaned_data[f] for f in services.EVENT_FIELDS if f in self.cleaned_data}
        data["slug"] = self.cleaned_data.get("slug")
        return data


class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ["name"]


class ProjectForm(forms.ModelForm):
    image_urls_text = forms.CharField(
        label="Image gallery URLs",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "One URL per line"}),
    )
    tech_tags_text = forms.CharField(
        label="Tech tags", required=False, help_text="Comma separated, e.g. django, postgres, htmx"
    )

    class Meta:
        model = Project
        fields = ["title", "tagline", "description", "track", "thumbnail_url", "demo_video_url", "repo_url", "live_url"]
        widgets = {"description": forms.Textarea(attrs={"rows": 10})}
        labels = {
            "tagline": "One line",
            "thumbnail_url": "Cover image",
            "demo_video_url": "Demo video",
            "repo_url": "Repository",
            "live_url": "Live address",
        }
        help_texts = {
            "tagline": "What it does, in a sentence. Shown in the gallery.",
            "thumbnail_url": "An address of an image. Without one the gallery draws a cover from the title.",
            "repo_url": "Judges open this first.",
        }

    def __init__(self, *args, event: Event, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.fields["track"].queryset = event.tracks.all()
        self.fields["track"].required = event.tracks.exists()
        if self.instance.pk:
            self.fields["image_urls_text"].initial = "\n".join(self.instance.image_urls or [])
            self.fields["tech_tags_text"].initial = ", ".join(self.instance.tech_tags or [])
        # A draft may leave required questions open; a submission may not.
        # The service decides. The form only marks the fields to match.
        closing = "submit" in self.data or (self.instance.pk and self.instance.status == Project.Status.SUBMITTED)
        for q in event.custom_questions.all():
            self.fields[f"q_{q.id}"] = self._question_field(q, bool(closing))
            if self.instance.pk:
                ans = self.instance.answers.filter(question=q).first()
                if ans:
                    self.fields[f"q_{q.id}"].initial = ans.value

    @staticmethod
    def _build_question_field(q: CustomQuestion, closing: bool = True):
        common = {"label": q.prompt, "required": q.required and closing, "help_text": q.help_text}
        if q.kind == CustomQuestion.Kind.TEXTAREA:
            return forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), **common)
        if q.kind == CustomQuestion.Kind.URL:
            # Text, checked by the service: the same addresses are accepted
            # and refused here as over the API.
            return forms.CharField(widget=forms.URLInput(), **common)
        if q.kind == CustomQuestion.Kind.CHOICE:
            choices = [(c, c) for c in q.choices]
            if not common["required"]:
                choices = [("", "No answer"), *choices]
            return forms.ChoiceField(choices=choices, **common)
        if q.kind == CustomQuestion.Kind.CHECKBOX:
            return forms.BooleanField(**{**common, "required": False})
        return forms.CharField(**common)

    @classmethod
    def _question_field(cls, q: CustomQuestion, closing: bool = True):
        field = cls._build_question_field(q, closing)
        # Marked on the form even while a draft may leave it open.
        field.needed = q.required
        return field

    def data_dict(self) -> dict:
        d = {
            k: self.cleaned_data[k]
            for k in (
                "title",
                "tagline",
                "description",
                "track",
                "thumbnail_url",
                "demo_video_url",
                "repo_url",
                "live_url",
            )
        }
        d["image_urls"] = self.cleaned_data.get("image_urls_text", "")
        d["tech_tags"] = self.cleaned_data.get("tech_tags_text", "")
        return d

    def answers(self) -> dict[int, str]:
        return {q.id: str(self.cleaned_data.get(f"q_{q.id}", "") or "") for q in self.event.custom_questions.all()}


class JudgeInviteForm(forms.Form):
    email = forms.EmailField(
        help_text="An account is created if none exists. Create a sign-in link for them and send it yourself."
    )
    name = forms.CharField(required=False)
    tracks = forms.ModelMultipleChoiceField(
        queryset=Track.objects.none(), required=False, help_text="Leave empty to allow every track."
    )

    def __init__(self, *args, event: Event, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tracks"].queryset = event.tracks.all()


class AssignForm(forms.Form):
    reviews_per_project = forms.IntegerField(min_value=1, max_value=20, initial=3)
    batch = forms.CharField(required=False, max_length=40, help_text="Label for this run, e.g. batch-1")
    seed = forms.IntegerField(
        required=False, min_value=0, max_value=2**31 - 1, help_text="Optional. Same seed, same assignment."
    )


class CriterionForm(forms.Form):
    key = forms.SlugField(max_length=40)
    name = forms.CharField(max_length=120)
    weight = forms.DecimalField(max_digits=6, decimal_places=2, min_value=0, initial=1)
    description = forms.CharField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The rubric is edited in a table whose column heads are the only visible labels.
        for name, field in self.fields.items():
            field.widget.attrs.setdefault("aria-label", field.label or name.replace("_", " ").capitalize())


CriterionFormSet = forms.formset_factory(CriterionForm, extra=1, can_delete=True)
