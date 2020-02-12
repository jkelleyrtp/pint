"""
    pint.definitions
    ~~~~~~~~~~~~~~~~

    Functions and classes related to unit definitions.

    :copyright: 2016 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from collections import namedtuple

from .converters import LogarithmicConverter, OffsetConverter, ScaleConverter
from .errors import DefinitionSyntaxError
from .util import ParserHelper, UnitsContainer, _is_dim


class ParsedDefinition(
    namedtuple("ParsedDefinition", "name symbol aliases value rhs_parts")
):
    """Splits a definition into the constitutive parts.

    A definition is given as a string with equalities in a single line.

        ---------------> rhs
    a = b = c = d = e
    |   |   |   -------> aliases (optional)
    |   |   |
    |   |   -----------> symbol (use "_" to avoid specifying a symbol)
    |   |
    |   ---------------> value
    |
    -------------------> name

    Attributes
    ----------
    name : str
    value : str
    symbol : str or None
    aliases : tuple of str
    rhs : tuple of str
    """

    @classmethod
    def from_string(cls, definition):
        name, definition = definition.split("=", 1)
        name = name.strip()

        rhs_parts = tuple(res.strip() for res in definition.split("="))

        value, aliases = rhs_parts[0], tuple([x for x in rhs_parts[1:] if x != ""])
        symbol, aliases = (aliases[0], aliases[1:]) if aliases else (None, aliases)
        if symbol == "_":
            symbol = None
        aliases = tuple([x for x in aliases if x != "_"])

        return cls(name, symbol, aliases, value, rhs_parts)


class _NotNumeric(Exception):
    """Internal exception. Do not expose outside Pint
    """

    def __init__(self, value):
        self.value = value


def numeric_parse(s):
    """Try parse a string into a number (without using eval).

    Parameters
    ----------
    s : str

    Returns
    -------
    Number

    Raises
    ------
    _NotNumeric
        If the string cannot be parsed as a number.
    """
    ph = ParserHelper.from_string(s)

    if len(ph):
        raise _NotNumeric(s)

    return ph.scale


class Definition:
    """Base class for definitions.

    Parameters
    ----------
    name : str
        Canonical name of the unit/prefix/etc.
    symbol : str or None
        A short name or symbol for the definition.
    aliases : iterable of str
        Other names for the unit/prefix/etc.
    converter : callable or Converter or None
        an instance of Converter.
    """

    def __init__(self, name, symbol, aliases, converter):

        if isinstance(converter, str):
            raise TypeError(
                "The converter parameter cannot be an instance of `str`. Use `from_string` method"
            )

        self._name = name
        self._symbol = symbol
        self._aliases = aliases
        self._converter = converter

    @property
    def is_multiplicative(self):
        return self._converter.is_multiplicative

    @classmethod
    def from_string(cls, definition):
        """Parse a definition into alias, dimension, prefix or unit.

        Parameters
        ----------
        definition : str or ParsedDefinition

        Returns
        -------
        Definition or subclass of Definition
        """

        if isinstance(definition, str):
            definition = ParsedDefinition.from_string(definition)

        if definition.name.startswith("@alias "):
            return AliasDefinition.from_string(definition)

        elif definition.name.startswith("["):
            return DimensionDefinition.from_string(definition)

        elif definition.name.endswith("-"):
            return PrefixDefinition.from_string(definition)

        else:
            return UnitDefinition.from_string(definition)

    @property
    def name(self):
        return self._name

    @property
    def symbol(self):
        return self._symbol or self._name

    @property
    def has_symbol(self):
        return bool(self._symbol)

    @property
    def aliases(self):
        return self._aliases

    def add_aliases(self, *alias):
        alias = tuple(a for a in alias if a not in self._aliases)
        self._aliases = self._aliases + alias

    @property
    def converter(self):
        return self._converter

    def __str__(self):
        return self.name


class PrefixDefinition(Definition):
    """Definition of a prefix.

    <prefix>- = <amount> [= <symbol>] [= <alias>] [ = <alias> ] [...]

    Example:
        deca- =  1e+1  = da- = deka-
    """

    @classmethod
    def from_string(cls, definition):
        if isinstance(definition, str):
            definition = ParsedDefinition.from_string(definition)

        aliases = tuple(alias.strip("-") for alias in definition.aliases)
        if definition.symbol:
            symbol = definition.symbol.strip("-")
        else:
            symbol = definition.symbol

        try:
            converter = ScaleConverter(numeric_parse(definition.value))
        except _NotNumeric as ex:
            raise ValueError(
                f"Prefix definition ('{definition.name}') must contain only numbers, not {ex.value}"
            )

        return cls(definition.name.rstrip("-"), symbol, aliases, converter)


class UnitDefinition(Definition):
    """Definition of a unit.

    <canonical name> = <relation to another unit or dimension> [= <symbol>] [= <alias>] [ = <alias> ] [...]

    Example:
        millennium = 1e3 * year = _ = millennia

    Parameters
    ----------
    reference : UnitsContainer
        Reference units.
    is_base : bool
        Indicates if it is a base unit.

    """

    def __init__(self, name, symbol, aliases, converter, reference=None, is_base=False):
        self.reference = reference
        self.is_base = is_base

        super().__init__(name, symbol, aliases, converter)

    @classmethod
    def from_string(cls, definition):
        if isinstance(definition, str):
            definition = ParsedDefinition.from_string(definition)

        if ";" in definition.value:
            [converter, modifiers] = definition.value.split(";", 1)

            try:
                modifiers = dict(
                    (key.strip(), numeric_parse(value))
                    for key, value in (part.split(":") for part in modifiers.split(";"))
                )
            except _NotNumeric as ex:
                raise ValueError(
                    f"Unit definition ('{definition.name}') must contain only numbers in modifier, not {ex.value}"
                )

        else:
            converter = definition.value
            modifiers = {}

        converter = ParserHelper.from_string(converter)

        if not any(_is_dim(key) for key in converter.keys()):
            is_base = False
        elif all(_is_dim(key) for key in converter.keys()):
            is_base = True
        else:
            raise DefinitionSyntaxError(
                "Cannot mix dimensions and units in the same definition. "
                "Base units must be referenced only to dimensions. "
                "Derived units must be referenced only to units."
            )

        reference = UnitsContainer(converter)

        if not modifiers:
            converter = ScaleConverter(converter.scale)

        elif "offset" in modifiers:
            if modifiers.get("offset", 0.0) != 0.0:
                converter = OffsetConverter(converter.scale, modifiers["offset"])
            else:
                converter = ScaleConverter(converter.scale)

        elif "logbase" in modifiers and "logfactor" in modifiers:
            converter = LogarithmicConverter(
                converter.scale, modifiers["logbase"], modifiers["logfactor"]
            )

        else:
            raise DefinitionSyntaxError("Unable to assing a converter to the unit")

        return cls(
            definition.name,
            definition.symbol,
            definition.aliases,
            converter,
            reference,
            is_base,
        )


class DimensionDefinition(Definition):
    """Definition of a dimension.

    [dimension name] = <relation to other dimensions>

    Example:
        [density] = [mass] / [volume]
    """

    def __init__(self, name, symbol, aliases, converter, reference=None, is_base=False):
        self.reference = reference
        self.is_base = is_base

        super().__init__(name, symbol, aliases, converter=None)

    @classmethod
    def from_string(cls, definition):
        if isinstance(definition, str):
            definition = ParsedDefinition.from_string(definition)

        converter = ParserHelper.from_string(definition.value)

        if not converter:
            is_base = True
        elif all(_is_dim(key) for key in converter.keys()):
            is_base = False
        else:
            raise DefinitionSyntaxError(
                "Base dimensions must be referenced to None. "
                "Derived dimensions must only be referenced "
                "to dimensions."
            )
        reference = UnitsContainer(converter)

        return cls(
            definition.name,
            definition.symbol,
            definition.aliases,
            converter,
            reference,
            is_base,
        )


class AliasDefinition(Definition):
    """Additional alias(es) for an already existing unit.

    @alias <canonical name or previous alias> = <alias> [ = <alias> ] [...]

    Example:
        @alias meter = my_meter
    """

    def __init__(self, name, aliases):
        super().__init__(name=name, symbol=None, aliases=aliases, converter=None)

    @classmethod
    def from_string(cls, definition):

        if isinstance(definition, str):
            definition = ParsedDefinition.from_string(definition)

        name = definition.name[len("@alias ") :].lstrip()
        return AliasDefinition(name, tuple(definition.rhs_parts))
