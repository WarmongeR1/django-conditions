"""
:Created: 6 December 2014
:Author: Lucas Connors

"""

import json

from django import forms
from django.conf import settings
from django.contrib.postgres.fields import JSONField as PJSONField
from django.core.exceptions import ValidationError
from django.db import models
from django.template.loader import render_to_string
from django.utils import six
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext_lazy as _

from .conditions import CompareCondition
from .exceptions import InvalidConditionError
from .forms import JSONFormField
from .lists import CondList
from .utils import _resolve_object_path
from .widgets import JSONWidget

__all__ = [
    'ConditionsWidget',
    'ConditionsFormField',
    'ConditionsField'
]


class TextJSONField(models.Field):
    """
    A field that will ensure the data entered into it is valid JSON.
    """
    default_error_messages = {
        'invalid': _("'%s' is not a valid JSON string.")
    }
    description = "JSON object"

    def __init__(self, *args, **kwargs):
        if not kwargs.get('null', False):
            kwargs['default'] = kwargs.get('default', dict)
        self.encoder_kwargs = {
            'indent': kwargs.pop('indent',
                                 getattr(settings, 'JSONFIELD_INDENT', None)),
        }
        # This can be an object (probably a class), or a path which can be imported, resulting
        # in an object.
        encoder_class = kwargs.pop('encoder_class',
                                   getattr(settings, 'JSONFIELD_ENCODER_CLASS',
                                           None))
        if encoder_class:
            self.encoder_kwargs['cls'] = _resolve_object_path(encoder_class)

        self.decoder_kwargs = dict(
            kwargs.pop('decoder_kwargs',
                       getattr(settings, 'JSONFIELD_DECODER_KWARGS', {})))
        super().__init__(*args, **kwargs)
        self.validate(self.get_default(), None)

    def formfield(self, **kwargs):
        defaults = {
            'form_class': JSONFormField,
            'widget': JSONWidget
        }
        defaults.update(**kwargs)
        return super().formfield(**defaults)

    def validate(self, value, model_instance):
        if not self.null and value is None:
            raise ValidationError(self.error_messages['null'])
        try:
            self.get_prep_value(value)
        except ValueError:
            raise ValidationError(self.error_messages['invalid'] % value)

    def get_default(self):
        if self.has_default():
            default = self.default
            if callable(default):
                default = default()
            if isinstance(default, six.string_types):
                return json.loads(default, **self.decoder_kwargs)
            return json.loads(json.dumps(default, **self.encoder_kwargs),
                              **self.decoder_kwargs)
        return super().get_default()

    def get_internal_type(self):
        return 'TextField'

    def db_type(self, connection):
        if connection.vendor == 'postgresql':
            return 'text'
        if connection.vendor == 'mysql':
            return 'longtext'
        if connection.vendor == 'oracle':
            return 'long'
        return 'text'

    def from_db_value(self, value, expression, connection, context):
        if value is None:
            return None
        if isinstance(value, str):
            return json.loads(value, **self.decoder_kwargs)
        return value

    def get_db_prep_value(self, value, connection=None, prepared=None):
        return self.get_prep_value(value)

    def get_prep_value(self, value):
        if value is None:
            if not self.null and self.blank:
                return ""
            return None
        return json.dumps(value, **self.encoder_kwargs)

    def get_prep_lookup(self, lookup_type, value):
        if lookup_type in ["exact", "iexact", "in", "isnull"]:
            return value
        if lookup_type in ["contains", "icontains"]:
            if isinstance(value, (list, tuple)):
                raise TypeError(
                    "Lookup type %r not supported with argument of %s" % (
                        lookup_type, type(value).__name__
                    ))
                # Need a way co combine the values with '%', but don't escape that.
                return self.get_prep_value(value)[1:-1].replace(', ', r'%')
            if isinstance(value, dict):
                return self.get_prep_value(value)[1:-1]
            return self.get_prep_value(value)
        raise TypeError('Lookup type %r not supported' % lookup_type)

    def value_to_string(self, obj):
        return self._get_val_from_obj(obj)


class ConditionsWidget(JSONWidget):
    # TODO: Use template_name and refactor widget to use Django 1.11's new get_context() method
    # when Django 1.8-1.10 support is dropped
    # https://docs.djangoproject.com/en/1.11/ref/forms/widgets/#django.forms.Widget.get_context
    template_name_dj110_to_dj111_compat = 'conditions/conditions_widget.html'

    def __init__(self, *args, **kwargs):
        self.condition_definitions = kwargs.pop('condition_definitions', {})
        if 'attrs' not in kwargs:
            kwargs['attrs'] = {}
        if 'cols' not in kwargs['attrs']:
            kwargs['attrs']['cols'] = 50
        super(ConditionsWidget, self).__init__(*args, **kwargs)

    def render(self, name, value, attrs=None, renderer=None):
        if isinstance(value, CondList):
            value = value.encode()
        textarea = super(ConditionsWidget, self).render(name, value, attrs)

        condition_groups = []
        for groupname, group in self.condition_definitions.items():
            conditions_in_group = []
            for condstr, condition in group.items():
                conditions_in_group.append({
                    'condstr': condstr,
                    'key_required': 'true' if condition.key_required() else 'false',
                    'keys_allowed': condition.keys_allowed,
                    'key_example': condition.key_example(),
                    'operator_required': 'true' if issubclass(condition,
                                                              CompareCondition) else 'false',
                    'operators': condition.operators().keys() if issubclass(
                        condition, CompareCondition) else [],
                    'operand_example': condition.operand_example() if issubclass(
                        condition, CompareCondition) else '',
                    'help_text': condition.help_text(),
                    'description': condition.full_description(),
                })
            conditions_in_group = sorted(conditions_in_group,
                                         key=lambda x: x['condstr'])

            condition_groups.append({
                'groupname': groupname,
                'conditions': conditions_in_group,
            })
        condition_groups = sorted(condition_groups,
                                  key=lambda x: x['groupname'])

        context = {
            'textarea': textarea,
            'condition_groups': condition_groups,
        }

        return mark_safe(
            render_to_string(self.template_name_dj110_to_dj111_compat, context))


class ConditionsFormField(JSONFormField):

    def __init__(self, *args, **kwargs):
        self.condition_definitions = kwargs.pop('condition_definitions', {})
        if 'widget' not in kwargs:
            kwargs['widget'] = ConditionsWidget(
                condition_definitions=self.condition_definitions)
        super(ConditionsFormField, self).__init__(*args, **kwargs)

    def clean(self, value):
        """ Validate conditions by decoding result """
        cleaned_json = super(ConditionsFormField, self).clean(value)
        if cleaned_json is None:
            return

        try:
            CondList.decode(cleaned_json,
                            definitions=self.condition_definitions)
        except InvalidConditionError as e:
            raise forms.ValidationError(
                "Invalid conditions JSON: {error}".format(error=str(e)))
        else:
            return cleaned_json


class BaseConditionField(object):
    """
    ConditionsField stores information on when the "value" of the
    instance should be considered True.
    """

    def formfield(self, **kwargs):
        kwargs['condition_definitions'] = self.condition_definitions
        return ConditionsFormField(**kwargs)

    def pre_init(self, value, obj):
        value = super().pre_init(value, obj)
        if isinstance(value, dict):
            value = CondList.decode(value,
                                    definitions=self.condition_definitions)
        return value

    def to_python(self, value):
        return super().to_python(value)

    def dumps_for_display(self, value):
        if isinstance(value, CondList):
            value = value.encode()
        return super().dumps_for_display(value)

    def get_db_prep_value(self, value, connection, prepared=False):
        if isinstance(value, CondList):
            value = value.encode()
        return super().get_db_prep_value(value, connection, prepared)


class ConditionsField(BaseConditionField, TextJSONField):
    def __init__(self, *args, **kwargs):
        self.condition_definitions = kwargs.pop('definitions', {})
        super().__init__(*args, **kwargs)


class JSONBConditionsField(BaseConditionField, PJSONField):
    def __init__(self, *args, **kwargs):
        self.condition_definitions = kwargs.pop('definitions', {})
        super().__init__(*args, **kwargs)
