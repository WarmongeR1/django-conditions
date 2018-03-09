# -*- coding: utf-8 -*-

import json

from django.forms import widgets
from django.utils import six

from .utils import default


class JSONWidget(widgets.Textarea):

    def render(self, name, value, attrs=None, renderer=None):
        if value is None:
            value = ""
        if not isinstance(value, six.string_types):
            value = json.dumps(value,
                               ensure_ascii=False,
                               indent=2,
                               default=default)
        return super(JSONWidget, self).render(name, value, attrs)
