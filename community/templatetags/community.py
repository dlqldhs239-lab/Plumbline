from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def square(value):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return ""
    return v * v
