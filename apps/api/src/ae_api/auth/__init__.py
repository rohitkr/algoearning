"""Who is calling. Authenticators turn a request into an Identity (the auth provider's user id + profile);
deps.current_user maps that onto our users table. Clerk in all environments; the X-Dev-User header only in
development/test with DEV_AUTH=true. Swapping Clerk for our own auth later means adding one Authenticator."""

from .base import Authenticator, AuthError, Identity

__all__ = ["AuthError", "Authenticator", "Identity"]
