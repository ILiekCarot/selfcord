"""
The MIT License (MIT)

Copyright (c) 2015-2021 Rapptz
Copyright (c) 2021-present Pycord Development

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.
"""

from __future__ import annotations

import array
import asyncio
import collections.abc
import datetime
import functools
import itertools
import json
import re
import sys
import types
import unicodedata
import warnings
from base64 import b64encode
from bisect import bisect_left
from inspect import isawaitable as _isawaitable
from inspect import signature as _signature
from operator import attrgetter
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
    ForwardRef,
    Generic,
    Iterable,
    Iterator,
    Literal,
    Mapping,
    Protocol,
    Sequence,
    TypeVar,
    Union,
    overload,
)

from .errors import HTTPException, InvalidArgument

try:
    import msgspec
except ModuleNotFoundError:
    HAS_MSGSPEC = False
else:
    HAS_MSGSPEC = True


__all__ = (
    "parse_time",
    "warn_deprecated",
    "deprecated",
    "oauth_url",
    "snowflake_time",
    "time_snowflake",
    "find",
    "get",
    "get_or_fetch",
    "sleep_until",
    "utcnow",
    "resolve_invite",
    "resolve_template",
    "remove_markdown",
    "escape_markdown",
    "escape_mentions",
    "raw_mentions",
    "raw_channel_mentions",
    "raw_role_mentions",
    "as_chunks",
    "format_dt",
    "generate_snowflake",
    "basic_autocomplete",
    "filter_params",
)

DISCORD_EPOCH = 1420070400000


class _MissingSentinel:
    def __eq__(self, other) -> bool:
        return False

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "..."


MISSING: Any = _MissingSentinel()


class _cached_property:
    def __init__(self, function):
        self.function = function
        self.__doc__ = getattr(function, "__doc__")

    def __get__(self, instance, owner):
        if instance is None:
            return self

        value = self.function(instance)
        setattr(instance, self.function.__name__, value)

        return value


if TYPE_CHECKING:
    from typing_extensions import ParamSpec

    from .abc import Snowflake
    from .commands.context import AutocompleteContext
    from .commands.options import OptionChoice
    from .invite import Invite
    from .permissions import Permissions
    from .template import Template

    class _RequestLike(Protocol):
        headers: Mapping[str, Any]

    cached_property = property

    P = ParamSpec("P")

else:
    cached_property = _cached_property
    AutocompleteContext = Any
    OptionChoice = Any


T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)
_Iter = Union[Iterator[T], AsyncIterator[T]]


class CachedSlotProperty(Generic[T, T_co]):
    def __init__(self, name: str, function: Callable[[T], T_co]) -> None:
        self.name = name
        self.function = function
        self.__doc__ = getattr(function, "__doc__")

    @overload
    def __get__(
        self, instance: None, owner: type[T]
    ) -> CachedSlotProperty[T, T_co]: ...

    @overload
    def __get__(self, instance: T, owner: type[T]) -> T_co: ...

    def __get__(self, instance: T | None, owner: type[T]) -> Any:
        if instance is None:
            return self

        try:
            return getattr(instance, self.name)
        except AttributeError:
            value = self.function(instance)
            setattr(instance, self.name, value)
            return value


class classproperty(Generic[T_co]):
    def __init__(self, fget: Callable[[Any], T_co]) -> None:
        self.fget = fget

    def __get__(self, instance: Any | None, owner: type[Any]) -> T_co:
        return self.fget(owner)

    def __set__(self, instance, value) -> None:
        raise AttributeError("cannot set attribute")


def cached_slot_property(
    name: str,
) -> Callable[[Callable[[T], T_co]], CachedSlotProperty[T, T_co]]:
    def decorator(func: Callable[[T], T_co]) -> CachedSlotProperty[T, T_co]:
        return CachedSlotProperty(name, func)

    return decorator


class SequenceProxy(Generic[T_co], collections.abc.Sequence):
    """Read-only proxy of a Sequence."""

    def __init__(self, proxied: Sequence[T_co]):
        self.__proxied = proxied

    def __getitem__(self, idx: int) -> T_co:
        return self.__proxied[idx]

    def __len__(self) -> int:
        return len(self.__proxied)

    def __contains__(self, item: Any) -> bool:
        return item in self.__proxied

    def __iter__(self) -> Iterator[T_co]:
        return iter(self.__proxied)

    def __reversed__(self) -> Iterator[T_co]:
        return reversed(self.__proxied)

    def index(self, value: Any, *args, **kwargs) -> int:
        return self.__proxied.index(value, *args, **kwargs)

    def count(self, value: Any) -> int:
        return self.__proxied.count(value)


def delay_task(delay: float, func: Coroutine):
    async def inner_call():
        await asyncio.sleep(delay)
        try:
            await func
        except HTTPException:
            pass

    asyncio.create_task(inner_call())


@overload
def parse_time(timestamp: None) -> None: ...


@overload
def parse_time(timestamp: str) -> datetime.datetime: ...


@overload
def parse_time(timestamp: str | None) -> datetime.datetime | None: ...


def parse_time(timestamp: str | None) -> datetime.datetime | None:
    """A helper function to convert an ISO 8601 timestamp to a datetime object.

    Parameters
    ----------
    timestamp: Optional[:class:`str`]
        The timestamp to convert.

    Returns
    -------
    Optional[:class:`datetime.datetime`]
        The converted datetime object.
    """
    if timestamp:
        return datetime.datetime.fromisoformat(timestamp)
    return None


def copy_doc(original: Callable) -> Callable[[T], T]:
    def decorator(overridden: T) -> T:
        overridden.__doc__ = original.__doc__
        overridden.__signature__ = _signature(original)  # type: ignore
        return overridden

    return decorator


def warn_deprecated(
    name: str,
    instead: str | None = None,
    since: str | None = None,
    removed: str | None = None,
    reference: str | None = None,
    stacklevel: int = 3,
) -> None:
    """Warn about a deprecated function, with the ability to specify details about the deprecation. Emits a
    DeprecationWarning.

    Parameters
    ----------
    name: str
        The name of the deprecated function.
    instead: Optional[:class:`str`]
        A recommended alternative to the function.
    since: Optional[:class:`str`]
        The version in which the function was deprecated. This should be in the format ``major.minor(.patch)``, where
        the patch version is optional.
    removed: Optional[:class:`str`]
        The version in which the function is planned to be removed. This should be in the format
        ``major.minor(.patch)``, where the patch version is optional.
    reference: Optional[:class:`str`]
        A reference that explains the deprecation, typically a URL to a page such as a changelog entry or a GitHub
        issue/PR.
    stacklevel: :class:`int`
        The stacklevel kwarg passed to :func:`warnings.warn`. Defaults to 3.
    """
    warnings.simplefilter("always", DeprecationWarning)  # turn off filter
    message = f"{name} is deprecated"
    if since:
        message += f" since version {since}"
    if removed:
        message += f" and will be removed in version {removed}"
    if instead:
        message += f", consider using {instead} instead"
    message += "."
    if reference:
        message += f" See {reference} for more information."

    warnings.warn(message, stacklevel=stacklevel, category=DeprecationWarning)
    warnings.simplefilter("default", DeprecationWarning)  # reset filter


def deprecated(
    instead: str | None = None,
    since: str | None = None,
    removed: str | None = None,
    reference: str | None = None,
    stacklevel: int = 3,
    *,
    use_qualname: bool = True,
) -> Callable[[Callable[[P], T]], Callable[[P], T]]:
    """A decorator implementation of :func:`warn_deprecated`. This will automatically call :func:`warn_deprecated` when
    the decorated function is called.

    Parameters
    ----------
    instead: Optional[:class:`str`]
        A recommended alternative to the function.
    since: Optional[:class:`str`]
        The version in which the function was deprecated. This should be in the format ``major.minor(.patch)``, where
        the patch version is optional.
    removed: Optional[:class:`str`]
        The version in which the function is planned to be removed. This should be in the format
        ``major.minor(.patch)``, where the patch version is optional.
    reference: Optional[:class:`str`]
        A reference that explains the deprecation, typically a URL to a page such as a changelog entry or a GitHub
        issue/PR.
    stacklevel: :class:`int`
        The stacklevel kwarg passed to :func:`warnings.warn`. Defaults to 3.
    use_qualname: :class:`bool`
        Whether to use the qualified name of the function in the deprecation warning. If ``False``, the short name of
        the function will be used instead. For example, __qualname__ will display as ``Client.login`` while __name__
        will display as ``login``. Defaults to ``True``.
    """

    def actual_decorator(func: Callable[[P], T]) -> Callable[[P], T]:
        @functools.wraps(func)
        def decorated(*args: P.args, **kwargs: P.kwargs) -> T:
            warn_deprecated(
                name=func.__qualname__ if use_qualname else func.__name__,
                instead=instead,
                since=since,
                removed=removed,
                reference=reference,
                stacklevel=stacklevel,
            )
            return func(*args, **kwargs)

        return decorated

    return actual_decorator


def oauth_url(
    client_id: int | str,
    *,
    permissions: Permissions = MISSING,
    guild: Snowflake = MISSING,
    redirect_uri: str = MISSING,
    scopes: Iterable[str] = MISSING,
    disable_guild_select: bool = False,
) -> str:
    """A helper function that returns the OAuth2 URL for inviting the bot
    into guilds.

    Parameters
    ----------
    client_id: Union[:class:`int`, :class:`str`]
        The client ID for your bot.
    permissions: :class:`~discord.Permissions`
        The permissions you're requesting. If not given then you won't be requesting any
        permissions.
    guild: :class:`~discord.abc.Snowflake`
        The guild to pre-select in the authorization screen, if available.
    redirect_uri: :class:`str`
        An optional valid redirect URI.
    scopes: Iterable[:class:`str`]
        An optional valid list of scopes. Defaults to ``('bot',)``.

        .. versionadded:: 1.7
    disable_guild_select: :class:`bool`
        Whether to disallow the user from changing the guild dropdown.

        .. versionadded:: 2.0

    Returns
    -------
    :class:`str`
        The OAuth2 URL for inviting the bot into guilds.
    """
    url = f"https://discord.com/oauth2/authorize?client_id={client_id}"
    url += f"&scope={'+'.join(scopes or ('bot',))}"
    if permissions is not MISSING:
        url += f"&permissions={permissions.value}"
    if guild is not MISSING:
        url += f"&guild_id={guild.id}"
    if redirect_uri is not MISSING:
        from urllib.parse import urlencode

        url += f"&response_type=code&{urlencode({'redirect_uri': redirect_uri})}"
    if disable_guild_select:
        url += "&disable_guild_select=true"
    return url


def snowflake_time(id: int) -> datetime.datetime:
    """Converts a Discord snowflake ID to a UTC-aware datetime object.

    Parameters
    ----------
    id: :class:`int`
        The snowflake ID.

    Returns
    -------
    :class:`datetime.datetime`
        An aware datetime in UTC representing the creation time of the snowflake.
    """
    timestamp = ((id >> 22) + DISCORD_EPOCH) / 1000
    return datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)


def time_snowflake(dt: datetime.datetime, high: bool = False) -> int:
    """Returns a numeric snowflake pretending to be created at the given date.

    When using as the lower end of a range, use ``time_snowflake(high=False) - 1``
    to be inclusive, ``high=True`` to be exclusive.

    When using as the higher end of a range, use ``time_snowflake(high=True) + 1``
    to be inclusive, ``high=False`` to be exclusive

    Parameters
    ----------
    dt: :class:`datetime.datetime`
        A datetime object to convert to a snowflake.
        If naive, the timezone is assumed to be local time.
    high: :class:`bool`
        Whether to set the lower 22 bit to high or low.

    Returns
    -------
    :class:`int`
        The snowflake representing the time given.
    """
    discord_millis = int(dt.timestamp() * 1000 - DISCORD_EPOCH)
    return (discord_millis << 22) + (2**22 - 1 if high else 0)


def find(predicate: Callable[[T], Any], seq: Iterable[T]) -> T | None:
    """A helper to return the first element found in the sequence
    that meets the predicate. For example: ::

        member = discord.utils.find(lambda m: m.name == 'Mighty', channel.guild.members)

    would find the first :class:`~discord.Member` whose name is 'Mighty' and return it.
    If an entry is not found, then ``None`` is returned.

    This is different from :func:`py:filter` due to the fact it stops the moment it finds
    a valid entry.

    Parameters
    ----------
    predicate
        A function that returns a boolean-like result.
    seq: :class:`collections.abc.Iterable`
        The iterable to search through.
    """

    for element in seq:
        if predicate(element):
            return element
    return None


def get(iterable: Iterable[T], **attrs: Any) -> T | None:
    r"""A helper that returns the first element in the iterable that meets
    all the traits passed in ``attrs``. This is an alternative for
    :func:`~discord.utils.find`.

    When multiple attributes are specified, they are checked using
    logical AND, not logical OR. Meaning they have to meet every
    attribute passed in and not one of them.

    To have a nested attribute search (i.e. search by ``x.y``) then
    pass in ``x__y`` as the keyword argument.

    If nothing is found that matches the attributes passed, then
    ``None`` is returned.

    Examples
    ---------

    Basic usage:

    .. code-block:: python3

        member = discord.utils.get(message.guild.members, name='Foo')

    Multiple attribute matching:

    .. code-block:: python3

        channel = discord.utils.get(guild.voice_channels, name='Foo', bitrate=64000)

    Nested attribute matching:

    .. code-block:: python3

        channel = discord.utils.get(client.get_all_channels(), guild__name='Cool', name='general')

    Parameters
    -----------
    iterable
        An iterable to search through.
    \*\*attrs
        Keyword arguments that denote attributes to search with.
    """

    # global -> local
    _all = all
    attrget = attrgetter

    # Special case the single element call
    if len(attrs) == 1:
        k, v = attrs.popitem()
        pred = attrget(k.replace("__", "."))
        for elem in iterable:
            if pred(elem) == v:
                return elem
        return None

    converted = [
        (attrget(attr.replace("__", ".")), value) for attr, value in attrs.items()
    ]

    for elem in iterable:
        if _all(pred(elem) == value for pred, value in converted):
            return elem
    return None


async def get_or_fetch(obj, attr: str, id: int, *, default: Any = MISSING) -> Any:
    """|coro|

    Attempts to get an attribute from the object in cache. If it fails, it will attempt to fetch it.
    If the fetch also fails, an error will be raised.

    Parameters
    ----------
    obj: Any
        The object to use the get or fetch methods in
    attr: :class:`str`
        The attribute to get or fetch. Note the object must have both a ``get_`` and ``fetch_`` method for this attribute.
    id: :class:`int`
        The ID of the object
    default: Any
        The default value to return if the object is not found, instead of raising an error.

    Returns
    -------
    Any
        The object found or the default value.

    Raises
    ------
    :exc:`AttributeError`
        The object is missing a ``get_`` or ``fetch_`` method
    :exc:`NotFound`
        Invalid ID for the object
    :exc:`HTTPException`
        An error occurred fetching the object
    :exc:`Forbidden`
        You do not have permission to fetch the object

    Examples
    --------

    Getting a guild from a guild ID: ::

        guild = await utils.get_or_fetch(client, 'guild', guild_id)

    Getting a channel from the guild. If the channel is not found, return None: ::

        channel = await utils.get_or_fetch(guild, 'channel', channel_id, default=None)
    """
    getter = getattr(obj, f"get_{attr}")(id)
    if getter is None:
        try:
            getter = await getattr(obj, f"fetch_{attr}")(id)
        except AttributeError:
            getter = await getattr(obj, f"_fetch_{attr}")(id)
            if getter is None:
                raise ValueError(f"Could not find {attr} with id {id} on {obj}")
        except (HTTPException, ValueError):
            if default is not MISSING:
                return default
            else:
                raise
    return getter


def _unique(iterable: Iterable[T]) -> list[T]:
    return [x for x in dict.fromkeys(iterable)]


def _get_as_snowflake(data: Any, key: str) -> int | None:
    try:
        value = data[key]
    except KeyError:
        return None
    else:
        return value and int(value)


def _get_mime_type_for_image(data: bytes):
    if data.startswith(b"\x89\x50\x4e\x47\x0d\x0a\x1a\x0a"):
        return "image/png"
    elif data[0:3] == b"\xff\xd8\xff" or data[6:10] in (b"JFIF", b"Exif"):
        return "image/jpeg"
    elif data.startswith((b"\x47\x49\x46\x38\x37\x61", b"\x47\x49\x46\x38\x39\x61")):
        return "image/gif"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    else:
        raise InvalidArgument("Unsupported image type given")


def _bytes_to_base64_data(data: bytes) -> str:
    fmt = "data:{mime};base64,{data}"
    mime = _get_mime_type_for_image(data)
    b64 = b64encode(data).decode("ascii")
    return fmt.format(mime=mime, data=b64)


if HAS_MSGSPEC:

    def _to_json(obj: Any) -> str:  # type: ignore
        return msgspec.json.encode(obj).decode("utf-8")

    _from_json = msgspec.json.decode  # type: ignore

else:

    def _to_json(obj: Any) -> str:
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=True)

    _from_json = json.loads


def _parse_ratelimit_header(request: Any, *, use_clock: bool = False) -> float:
    reset_after: str | None = request.headers.get("X-Ratelimit-Reset-After")
    if not use_clock and reset_after:
        return float(reset_after)
    utc = datetime.timezone.utc
    now = datetime.datetime.now(utc)
    reset = datetime.datetime.fromtimestamp(
        float(request.headers["X-Ratelimit-Reset"]), utc
    )
    return (reset - now).total_seconds()


async def maybe_coroutine(f, *args, **kwargs):
    value = f(*args, **kwargs)
    if _isawaitable(value):
        return await value
    else:
        return value


async def async_all(gen, *, check=_isawaitable):
    for elem in gen:
        if check(elem):
            elem = await elem
        if not elem:
            return False
    return True


async def sane_wait_for(futures, *, timeout):
    ensured = [asyncio.ensure_future(fut) for fut in futures]
    done, pending = await asyncio.wait(
        ensured, timeout=timeout, return_when=asyncio.ALL_COMPLETED
    )

    if len(pending) != 0:
        raise asyncio.TimeoutError()

    return done


def get_slots(cls: type[Any]) -> Iterator[str]:
    for mro in reversed(cls.__mro__):
        try:
            yield from mro.__slots__
        except AttributeError:
            continue


def compute_timedelta(dt: datetime.datetime):
    if dt.tzinfo is None:
        dt = dt.astimezone()
    now = datetime.datetime.now(datetime.timezone.utc)
    return max((dt - now).total_seconds(), 0)


async def sleep_until(when: datetime.datetime, result: T | None = None) -> T | None:
    """|coro|

    Sleep until a specified time.

    If the time supplied is in the past this function will yield instantly.

    .. versionadded:: 1.3

    Parameters
    ----------
    when: :class:`datetime.datetime`
        The timestamp in which to sleep until. If the datetime is naive then
        it is assumed to be local time.
    result: Any
        If provided is returned to the caller when the coroutine completes.
    """
    delta = compute_timedelta(when)
    return await asyncio.sleep(delta, result)


def utcnow() -> datetime.datetime:
    """A helper function to return an aware UTC datetime representing the current time.

    This should be preferred to :meth:`datetime.datetime.utcnow` since it is an aware
    datetime, compared to the naive datetime in the standard library.

    .. versionadded:: 2.0

    Returns
    -------
    :class:`datetime.datetime`
        The current aware datetime in UTC.
    """
    return datetime.datetime.now(datetime.timezone.utc)


def valid_icon_size(size: int) -> bool:
    """Icons must be power of 2 within [16, 4096]."""
    return not size & (size - 1) and 4096 >= size >= 16


class SnowflakeList(array.array):
    """Internal data storage class to efficiently store a list of snowflakes.

    This should have the following characteristics:

    - Low memory usage
    - O(n) iteration (obviously)
    - O(n log n) initial creation if data is unsorted
    - O(log n) search and indexing
    - O(n) insertion
    """

    __slots__ = ()

    if TYPE_CHECKING:

        def __init__(self, data: Iterable[int], *, is_sorted: bool = False): ...

    def __new__(cls, data: Iterable[int], *, is_sorted: bool = False):
        return array.array.__new__(cls, "Q", data if is_sorted else sorted(data))  # type: ignore

    def add(self, element: int) -> None:
        i = bisect_left(self, element)
        self.insert(i, element)

    def get(self, element: int) -> int | None:
        i = bisect_left(self, element)
        return self[i] if i != len(self) and self[i] == element else None

    def has(self, element: int) -> bool:
        i = bisect_left(self, element)
        return i != len(self) and self[i] == element


_IS_ASCII = re.compile(r"^[\x00-\x7f]+$")


def _string_width(string: str, *, _IS_ASCII=_IS_ASCII) -> int:
    """Returns string's width."""
    match = _IS_ASCII.match(string)
    if match:
        return match.endpos

    UNICODE_WIDE_CHAR_TYPE = "WFA"
    func = unicodedata.east_asian_width
    return sum(2 if func(char) in UNICODE_WIDE_CHAR_TYPE else 1 for char in string)


def resolve_invite(invite: Invite | str) -> str:
    """
    Resolves an invite from a :class:`~discord.Invite`, URL or code.

    Parameters
    ----------
    invite: Union[:class:`~discord.Invite`, :class:`str`]
        The invite.

    Returns
    -------
    :class:`str`
        The invite code.
    """
    from .invite import Invite  # circular import

    if isinstance(invite, Invite):
        return invite.code
    rx = r"(?:https?\:\/\/)?discord(?:\.gg|(?:app)?\.com\/invite)\/(.+)"
    m = re.match(rx, invite)
    if m:
        return m.group(1)
    return invite


def resolve_template(code: Template | str) -> str:
    """
    Resolves a template code from a :class:`~discord.Template`, URL or code.

    .. versionadded:: 1.4

    Parameters
    ----------
    code: Union[:class:`~discord.Template`, :class:`str`]
        The code.

    Returns
    -------
    :class:`str`
        The template code.
    """
    from .template import Template  # circular import

    if isinstance(code, Template):
        return code.code
    rx = r"(?:https?\:\/\/)?discord(?:\.new|(?:app)?\.com\/template)\/(.+)"
    m = re.match(rx, code)
    if m:
        return m.group(1)
    return code


_MARKDOWN_ESCAPE_SUBREGEX = "|".join(
    r"\{0}(?=([\s\S]*((?<!\{0})\{0})))".format(c) for c in ("*", "`", "_", "~", "|")
)

# regular expression for finding and escaping links in markdown
# note: technically, brackets are allowed in link text.
# perhaps more concerning, parentheses are also allowed in link destination.
# this regular expression matches neither of those.
# this page provides a good reference: http://blog.michaelperrin.fr/2019/02/04/advanced-regular-expressions/
_MARKDOWN_ESCAPE_LINKS = r"""
\[  # matches link text
    [^\[\]]* # link text can contain anything but brackets
\]
\(  # matches link destination
    [^\(\)]+ # link destination cannot contain parentheses
\)"""  # note 2: make sure this regex is consumed in re.X (extended mode) since it has whitespace and comments

_MARKDOWN_ESCAPE_COMMON = rf"^>(?:>>)?\s|{_MARKDOWN_ESCAPE_LINKS}"

_MARKDOWN_ESCAPE_REGEX = re.compile(
    rf"(?P<markdown>{_MARKDOWN_ESCAPE_SUBREGEX}|{_MARKDOWN_ESCAPE_COMMON})",
    re.MULTILINE | re.X,
)

_URL_REGEX = r"(?P<url><[^: >]+:\/[^ >]+>|(?:https?|steam):\/\/[^\s<]+[^<.,:;\"\'\]\s])"

_MARKDOWN_STOCK_REGEX = rf"(?P<markdown>[_\\~|\*`]|{_MARKDOWN_ESCAPE_COMMON})"


def remove_markdown(text: str, *, ignore_links: bool = True) -> str:
    """A helper function that removes markdown characters.

    .. versionadded:: 1.7

    .. note::
            This function is not markdown aware and may remove meaning from the original text. For example,
            if the input contains ``10 * 5`` then it will be converted into ``10  5``.

    Parameters
    ----------
    text: :class:`str`
        The text to remove markdown from.
    ignore_links: :class:`bool`
        Whether to leave links alone when removing markdown. For example,
        if a URL in the text contains characters such as ``_`` then it will
        be left alone. Defaults to ``True``.

    Returns
    -------
    :class:`str`
        The text with the markdown special characters removed.
    """

    def replacement(match):
        groupdict = match.groupdict()
        return groupdict.get("url", "")

    regex = _MARKDOWN_STOCK_REGEX
    if ignore_links:
        regex = f"(?:{_URL_REGEX}|{regex})"
    return re.sub(regex, replacement, text, count=0, flags=re.MULTILINE)


def escape_markdown(
    text: str, *, as_needed: bool = False, ignore_links: bool = True
) -> str:
    r"""A helper function that escapes Discord's markdown.

    Parameters
    -----------
    text: :class:`str`
        The text to escape markdown from.
    as_needed: :class:`bool`
        Whether to escape the markdown characters as needed. This
        means that it does not escape extraneous characters if it's
        not necessary, e.g. ``**hello**`` is escaped into ``\*\*hello**``
        instead of ``\*\*hello\*\*``. Note however that this can open
        you up to some clever syntax abuse. Defaults to ``False``.
    ignore_links: :class:`bool`
        Whether to leave links alone when escaping markdown. For example,
        if a URL in the text contains characters such as ``_`` then it will
        be left alone. This option is not supported with ``as_needed``.
        Defaults to ``True``.

    Returns
    --------
    :class:`str`
        The text with the markdown special characters escaped with a slash.
    """

    if not as_needed:

        def replacement(match):
            groupdict = match.groupdict()
            is_url = groupdict.get("url")
            if is_url:
                return is_url
            return f"\\{groupdict['markdown']}"

        regex = _MARKDOWN_STOCK_REGEX
        if ignore_links:
            regex = f"(?:{_URL_REGEX}|{regex})"
        return re.sub(regex, replacement, text, count=0, flags=re.MULTILINE | re.X)
    else:
        text = re.sub(r"\\", r"\\\\", text)
        return _MARKDOWN_ESCAPE_REGEX.sub(r"\\\1", text)


def escape_mentions(text: str) -> str:
    """A helper function that escapes everyone, here, role, and user mentions.

    .. note::

        This does not include channel mentions.

    .. note::

        For more granular control over what mentions should be escaped
        within messages, refer to the :class:`~discord.AllowedMentions`
        class.

    Parameters
    ----------
    text: :class:`str`
        The text to escape mentions from.

    Returns
    -------
    :class:`str`
        The text with the mentions removed.
    """
    return re.sub(r"@(everyone|here|[!&]?[0-9]{17,20})", "@\u200b\\1", text)


def raw_mentions(text: str) -> list[int]:
    """Returns a list of user IDs matching ``<@user_id>`` in the string.

    .. versionadded:: 2.2

    Parameters
    ----------
    text: :class:`str`
        The string to get user mentions from.

    Returns
    -------
    List[:class:`int`]
        A list of user IDs found in the string.
    """
    return [int(x) for x in re.findall(r"<@!?([0-9]+)>", text)]


def raw_channel_mentions(text: str) -> list[int]:
    """Returns a list of channel IDs matching ``<@#channel_id>`` in the string.

    .. versionadded:: 2.2

    Parameters
    ----------
    text: :class:`str`
        The string to get channel mentions from.

    Returns
    -------
    List[:class:`int`]
        A list of channel IDs found in the string.
    """
    return [int(x) for x in re.findall(r"<#([0-9]+)>", text)]


def raw_role_mentions(text: str) -> list[int]:
    """Returns a list of role IDs matching ``<@&role_id>`` in the string.

    .. versionadded:: 2.2

    Parameters
    ----------
    text: :class:`str`
        The string to get role mentions from.

    Returns
    -------
    List[:class:`int`]
        A list of role IDs found in the string.
    """
    return [int(x) for x in re.findall(r"<@&([0-9]+)>", text)]


def _chunk(iterator: Iterator[T], max_size: int) -> Iterator[list[T]]:
    ret = []
    n = 0
    for item in iterator:
        ret.append(item)
        n += 1
        if n == max_size:
            yield ret
            ret = []
            n = 0
    if ret:
        yield ret


async def _achunk(iterator: AsyncIterator[T], max_size: int) -> AsyncIterator[list[T]]:
    ret = []
    n = 0
    async for item in iterator:
        ret.append(item)
        n += 1
        if n == max_size:
            yield ret
            ret = []
            n = 0
    if ret:
        yield ret


@overload
def as_chunks(iterator: Iterator[T], max_size: int) -> Iterator[list[T]]: ...


@overload
def as_chunks(iterator: AsyncIterator[T], max_size: int) -> AsyncIterator[list[T]]: ...


def as_chunks(iterator: _Iter[T], max_size: int) -> _Iter[list[T]]:
    """A helper function that collects an iterator into chunks of a given size.

    .. versionadded:: 2.0

    .. warning::

        The last chunk collected may not be as large as ``max_size``.

    Parameters
    ----------
    iterator: Union[:class:`collections.abc.Iterator`, :class:`collections.abc.AsyncIterator`]
        The iterator to chunk, can be sync or async.
    max_size: :class:`int`
        The maximum chunk size.

    Returns
    -------
    Union[:class:`collections.abc.Iterator`, :class:`collections.abc.AsyncIterator`]
        A new iterator which yields chunks of a given size.
    """
    if max_size <= 0:
        raise ValueError("Chunk sizes must be greater than 0.")

    if isinstance(iterator, AsyncIterator):
        return _achunk(iterator, max_size)
    return _chunk(iterator, max_size)


PY_310 = sys.version_info >= (3, 10)


def flatten_literal_params(parameters: Iterable[Any]) -> tuple[Any, ...]:
    params = []
    literal_cls = type(Literal[0])
    for p in parameters:
        if isinstance(p, literal_cls):
            params.extend(p.__args__)
        else:
            params.append(p)
    return tuple(params)


def normalise_optional_params(parameters: Iterable[Any]) -> tuple[Any, ...]:
    none_cls = type(None)
    return tuple(p for p in parameters if p is not none_cls) + (none_cls,)


def evaluate_annotation(
    tp: Any,
    globals: dict[str, Any],
    locals: dict[str, Any],
    cache: dict[str, Any],
    *,
    implicit_str: bool = True,
):
    if isinstance(tp, ForwardRef):
        tp = tp.__forward_arg__
        # ForwardRefs always evaluate their internals
        implicit_str = True

    if implicit_str and isinstance(tp, str):
        if tp in cache:
            return cache[tp]
        evaluated = eval(tp, globals, locals)
        cache[tp] = evaluated
        return evaluate_annotation(evaluated, globals, locals, cache)

    if hasattr(tp, "__args__"):
        implicit_str = True
        is_literal = False
        args = tp.__args__
        if not hasattr(tp, "__origin__"):
            if PY_310 and tp.__class__ is types.UnionType:  # type: ignore
                converted = Union[args]  # type: ignore
                return evaluate_annotation(converted, globals, locals, cache)

            return tp
        if tp.__origin__ is Union:
            try:
                if args.index(type(None)) != len(args) - 1:
                    args = normalise_optional_params(tp.__args__)
            except ValueError:
                pass
        if tp.__origin__ is Literal:
            if not PY_310:
                args = flatten_literal_params(tp.__args__)
            implicit_str = False
            is_literal = True

        evaluated_args = tuple(
            evaluate_annotation(arg, globals, locals, cache, implicit_str=implicit_str)
            for arg in args
        )

        if is_literal and not all(
            isinstance(x, (str, int, bool, type(None))) for x in evaluated_args
        ):
            raise TypeError(
                "Literal arguments must be of type str, int, bool, or NoneType."
            )

        if evaluated_args == args:
            return tp

        try:
            return tp.copy_with(evaluated_args)
        except AttributeError:
            return tp.__origin__[evaluated_args]

    return tp


def resolve_annotation(
    annotation: Any,
    globalns: dict[str, Any],
    localns: dict[str, Any] | None,
    cache: dict[str, Any] | None,
) -> Any:
    if annotation is None:
        return type(None)
    if isinstance(annotation, str):
        annotation = ForwardRef(annotation)

    locals = globalns if localns is None else localns
    if cache is None:
        cache = {}
    return evaluate_annotation(annotation, globalns, locals, cache)


TimestampStyle = Literal["f", "F", "d", "D", "t", "T", "R"]


def format_dt(
    dt: datetime.datetime | datetime.time, /, style: TimestampStyle | None = None
) -> str:
    """A helper function to format a :class:`datetime.datetime` for presentation within Discord.

    This allows for a locale-independent way of presenting data using Discord specific Markdown.

    +-------------+----------------------------+-----------------+
    |    Style    |       Example Output       |   Description   |
    +=============+============================+=================+
    | t           | 22:57                      | Short Time      |
    +-------------+----------------------------+-----------------+
    | T           | 22:57:58                   | Long Time       |
    +-------------+----------------------------+-----------------+
    | d           | 17/05/2016                 | Short Date      |
    +-------------+----------------------------+-----------------+
    | D           | 17 May 2016                | Long Date       |
    +-------------+----------------------------+-----------------+
    | f (default) | 17 May 2016 22:57          | Short Date Time |
    +-------------+----------------------------+-----------------+
    | F           | Tuesday, 17 May 2016 22:57 | Long Date Time  |
    +-------------+----------------------------+-----------------+
    | R           | 5 years ago                | Relative Time   |
    +-------------+----------------------------+-----------------+

    Note that the exact output depends on the user's locale setting in the client. The example output
    presented is using the ``en-GB`` locale.

    .. versionadded:: 2.0

    Parameters
    ----------
    dt: Union[:class:`datetime.datetime`, :class:`datetime.time`]
        The datetime to format.
    style: :class:`str`
        The style to format the datetime with.

    Returns
    -------
    :class:`str`
        The formatted string.
    """
    if isinstance(dt, datetime.time):
        dt = datetime.datetime.combine(datetime.datetime.now(), dt)
    if style is None:
        return f"<t:{int(dt.timestamp())}>"
    return f"<t:{int(dt.timestamp())}:{style}>"


def generate_snowflake(dt: datetime.datetime | None = None) -> int:
    """Returns a numeric snowflake pretending to be created at the given date but more accurate and random
    than :func:`time_snowflake`. If dt is not passed, it makes one from the current time using utcnow.

    Parameters
    ----------
    dt: :class:`datetime.datetime`
        A datetime object to convert to a snowflake.
        If naive, the timezone is assumed to be local time.

    Returns
    -------
    :class:`int`
        The snowflake representing the time given.
    """

    dt = dt or utcnow()
    return int(dt.timestamp() * 1000 - DISCORD_EPOCH) << 22 | 0x3FFFFF


V = Union[Iterable[OptionChoice], Iterable[str], Iterable[int], Iterable[float]]
AV = Awaitable[V]
Values = Union[V, Callable[[AutocompleteContext], Union[V, AV]], AV]
AutocompleteFunc = Callable[[AutocompleteContext], AV]
FilterFunc = Callable[[AutocompleteContext, Any], Union[bool, Awaitable[bool]]]


def basic_autocomplete(
    values: Values, *, filter: FilterFunc | None = None
) -> AutocompleteFunc:
    """A helper function to make a basic autocomplete for slash commands. This is a pretty standard autocomplete and
    will return any options that start with the value from the user, case-insensitive. If the ``values`` parameter is
    callable, it will be called with the AutocompleteContext.

    This is meant to be passed into the :attr:`discord.Option.autocomplete` attribute.

    Parameters
    ----------
    values: Union[Union[Iterable[:class:`.OptionChoice`], Iterable[:class:`str`], Iterable[:class:`int`], Iterable[:class:`float`]], Callable[[:class:`.AutocompleteContext`], Union[Union[Iterable[:class:`str`], Iterable[:class:`int`], Iterable[:class:`float`]], Awaitable[Union[Iterable[:class:`str`], Iterable[:class:`int`], Iterable[:class:`float`]]]]], Awaitable[Union[Iterable[:class:`str`], Iterable[:class:`int`], Iterable[:class:`float`]]]]
        Possible values for the option. Accepts an iterable of :class:`str`, a callable (sync or async) that takes a
        single argument of :class:`.AutocompleteContext`, or a coroutine. Must resolve to an iterable of :class:`str`.
    filter: Optional[Callable[[:class:`.AutocompleteContext`, Any], Union[:class:`bool`, Awaitable[:class:`bool`]]]]
        An optional callable (sync or async) used to filter the autocomplete options. It accepts two arguments:
        the :class:`.AutocompleteContext` and an item from ``values`` iteration treated as callback parameters. If ``None`` is provided, a default filter is used that includes items whose string representation starts with the user's input value, case-insensitive.

        .. versionadded:: 2.7

    Returns
    -------
    Callable[[:class:`.AutocompleteContext`], Awaitable[Union[Iterable[:class:`.OptionChoice`], Iterable[:class:`str`], Iterable[:class:`int`], Iterable[:class:`float`]]]]
        A wrapped callback for the autocomplete.

    Examples
    --------

    Basic usage:

    .. code-block:: python3

        Option(str, "color", autocomplete=basic_autocomplete(("red", "green", "blue")))

        # or

        async def autocomplete(ctx):
            return "foo", "bar", "baz", ctx.interaction.user.name

        Option(str, "name", autocomplete=basic_autocomplete(autocomplete))

    With filter parameter:

    .. code-block:: python3

        Option(str, "color", autocomplete=basic_autocomplete(("red", "green", "blue"), filter=lambda c, i: str(c.value or "") in i))

    .. versionadded:: 2.0

    Note
    ----
    Autocomplete cannot be used for options that have specified choices.
    """

    async def autocomplete_callback(ctx: AutocompleteContext) -> V:
        _values = values  # since we reassign later, python considers it local if we don't do this

        if callable(_values):
            _values = _values(ctx)
        if asyncio.iscoroutine(_values):
            _values = await _values

        if filter is None:

            def _filter(ctx: AutocompleteContext, item: Any) -> bool:
                item = getattr(item, "name", item)
                return str(item).lower().startswith(str(ctx.value or "").lower())

            gen = (val for val in _values if _filter(ctx, val))

        elif asyncio.iscoroutinefunction(filter):
            gen = (val for val in _values if await filter(ctx, val))

        elif callable(filter):
            gen = (val for val in _values if filter(ctx, val))

        else:
            raise TypeError("``filter`` must be callable.")

        return iter(itertools.islice(gen, 25))

    return autocomplete_callback


def filter_params(params, **kwargs):
    """A helper function to filter out and replace certain keyword parameters

    Parameters
    ----------
    params: Dict[str, Any]
        The initial parameters to filter.
    **kwargs: Dict[str, Optional[str]]
        Key to value pairs where the key's contents would be moved to the
        value, or if the value is None, remove key's contents (see code example).

    Example
    -------
    .. code-block:: python3

        >>> params = {"param1": 12, "param2": 13}
        >>> filter_params(params, param1="param3", param2=None)
        {'param3': 12}
        # values of 'param1' is moved to 'param3'
        # and values of 'param2' are completely removed.
    """
    for old_param, new_param in kwargs.items():
        if old_param in params:
            if new_param is None:
                params.pop(old_param)
            else:
                params[new_param] = params.pop(old_param)

    return params
