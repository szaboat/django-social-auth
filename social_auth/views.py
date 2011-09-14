"""Views

Notes:
    * Some views are marked to avoid csrf tocken check becuase they relay
      on third party providers that (if using POST) won't be sending crfs
      token back.
"""
from functools import wraps

from django.conf import settings
from django.http import HttpResponseRedirect, HttpResponse, \
                        HttpResponseServerError
from django.core.urlresolvers import reverse
from django.db import transaction
from django.contrib.auth import login, REDIRECT_FIELD_NAME
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt

from social_auth.backends import get_backend
from social_auth.utils import sanitize_redirect


def _setting(name, default=''):
    return getattr(settings, name, default)

DEFAULT_REDIRECT = _setting('SOCIAL_AUTH_LOGIN_REDIRECT_URL') or \
                   _setting('LOGIN_REDIRECT_URL')
NEW_USER_REDIRECT = _setting('SOCIAL_AUTH_NEW_USER_REDIRECT_URL')
NEW_ASSOCIATION_REDIRECT = _setting('SOCIAL_AUTH_NEW_ASSOCIATION_REDIRECT_URL')
DISCONNECT_REDIRECT_URL = _setting('SOCIAL_AUTH_DISCONNECT_REDIRECT_URL')
LOGIN_ERROR_URL = _setting('LOGIN_ERROR_URL', settings.LOGIN_URL)
COMPLETE_URL_NAME = _setting('SOCIAL_AUTH_COMPLETE_URL_NAME', 'socialauth_complete')
ASSOCIATE_URL_NAME = _setting('SOCIAL_AUTH_ASSOCIATE_URL_NAME',
                              'socialauth_associate_complete')
SOCIAL_AUTH_LAST_LOGIN = _setting('SOCIAL_AUTH_LAST_LOGIN',
                                  'social_auth_last_login_backend')
SESSION_EXPIRATION = _setting('SOCIAL_AUTH_SESSION_EXPIRATION', True)


def dsa_view(redirect_name=None):
    """Decorate djangos-social-auth views. Will check and retrieve backend
    or return HttpResponseServerError if backend is not found.

        redirect_name parameter is used to build redirect URL used by backend.
    """
    def dec(func):
        @wraps(func)
        def wrapper(request, backend, *args, **kwargs):
            if redirect_name:
                redirect = reverse(redirect_name, args=(backend,))
            else:
                redirect = request.path
            backend = get_backend(backend, request, redirect)

            if not backend:
                return HttpResponseServerError('Incorrect authentication ' + \
                                               'service')
            return func(request, backend, *args, **kwargs)
        return wrapper
    return dec


@dsa_view(COMPLETE_URL_NAME)
def auth(request, backend):
    """Start authentication process"""
    return auth_process(request, backend)


@csrf_exempt
@transaction.commit_on_success
@dsa_view()
def complete(request, backend):
    """Authentication complete view, override this view if transaction
    management doesn't suit your needs."""
    return complete_process(request, backend)


@login_required
@dsa_view(ASSOCIATE_URL_NAME)
def associate(request, backend):
    """Authentication starting process"""
    return auth_process(request, backend)


@csrf_exempt
@login_required
@dsa_view()
def associate_complete(request, backend):
    """Authentication complete process"""
    if auth_complete(request, backend, request.user):
        url = NEW_ASSOCIATION_REDIRECT if NEW_ASSOCIATION_REDIRECT else \
              request.session.pop(REDIRECT_FIELD_NAME, '') or \
              DEFAULT_REDIRECT
    else:
        url = LOGIN_ERROR_URL
    return HttpResponseRedirect(url)


@login_required
@dsa_view()
def disconnect(request, backend, association_id=None):
    """Disconnects given backend from current logged in user."""
    backend.disconnect(request.user, association_id)
    url = request.REQUEST.get(REDIRECT_FIELD_NAME, '') or \
          DISCONNECT_REDIRECT_URL or \
          DEFAULT_REDIRECT
    return HttpResponseRedirect(url)


def auth_process(request, backend):
    """Authenticate using social backend"""
    # Save any defined redirect_to value into session
    if REDIRECT_FIELD_NAME in request.REQUEST:
        data = request.POST if request.method == 'POST' else request.GET
        if REDIRECT_FIELD_NAME in data:
            # Check and sanitize a user-defined GET/POST redirect_to field value.
            redirect = sanitize_redirect(request.get_host(),
                                         data[REDIRECT_FIELD_NAME])
            request.session[REDIRECT_FIELD_NAME] = redirect or DEFAULT_REDIRECT

    if backend.uses_redirect:
        return HttpResponseRedirect(backend.auth_url())
    else:
        return HttpResponse(backend.auth_html(),
                            content_type='text/html;charset=UTF-8')


class HttpResponseException(Exception, HttpResponse):
    def __init__(self, response):
        self.response = response


def complete_process(request, backend):
    """Authentication complete process"""

    try:
        user = auth_complete(request, backend)
    except HttpResponseException, e :
        return e.response

    if user and getattr(user, 'is_active', True):
        login(request, user)
        # user.social_user is the used UserSocialAuth instance defined
        # in authenticate process
        social_user = user.social_user

        if SESSION_EXPIRATION :
            # Set session expiration date if present and not disabled by
            # setting. Use last social-auth instance for current provider,
            # users can associate several accounts with a same provider.
            if social_user.expiration_delta():
                request.session.set_expiry(social_user.expiration_delta())

        # store last login backend name in session
        request.session[SOCIAL_AUTH_LAST_LOGIN] = social_user.provider

        # Remove possible redirect URL from session, if this is a new account,
        # send him to the new-users-page if defined.
        url = NEW_USER_REDIRECT if NEW_USER_REDIRECT and \
                                   getattr(user, 'is_new', False) else \
              request.session.pop(REDIRECT_FIELD_NAME, '') or \
              DEFAULT_REDIRECT
    else:
        url = LOGIN_ERROR_URL
    return HttpResponseRedirect(url)


def auth_complete(request, backend, user=None):
    """Complete auth process. Return authenticated user or None."""
    if user and not user.is_authenticated():
        user = None

    try:
        user = backend.auth_complete(user=user)
    except ValueError, e:  # some Authentication error ocurred
        error_key = getattr(settings, 'SOCIAL_AUTH_ERROR_KEY', None)
        if error_key:  # store error in session
            request.session[error_key] = str(e)
    return user
