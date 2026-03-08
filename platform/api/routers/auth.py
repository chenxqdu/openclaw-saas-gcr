"""Authentication router"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.tenant import TenantAllowedEmail, TenantMember
from api.models.user import UserCreate, UserLogin, UserResponse
from api.services.auth_svc import authenticate_user, create_access_token, create_user

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def signup(user_data: UserCreate, db: AsyncSession = Depends(get_db)):
    """Register a new user.

    Only emails pre-approved by a platform admin (added to a tenant's allowed list)
    can sign up. After registration, the user is automatically added as a member
    of the associated tenant.
    """
    # Check if this email is in any tenant's allowed list
    result = await db.execute(
        select(TenantAllowedEmail).where(
            TenantAllowedEmail.email == user_data.email,
            TenantAllowedEmail.used == False,  # noqa: E712
        )
    )
    allowed = result.scalars().all()

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This email is not authorized to sign up. Contact your platform admin.",
        )

    # Create the user
    user = await create_user(db, user_data)

    # Auto-associate user with all tenants that allowed this email
    for entry in allowed:
        member = TenantMember(
            tenant_id=entry.tenant_id,
            user_id=user.id,
            role=entry.role,
            invited_by=entry.added_by,
        )
        db.add(member)

        # Mark as used
        entry.used = True
        entry.used_at = datetime.utcnow()

    await db.flush()
    await db.refresh(user)
    return user


@router.post("/login")
async def login(credentials: UserLogin, db: AsyncSession = Depends(get_db)):
    """Login and get JWT access token"""
    user = await authenticate_user(db, credentials.email, credentials.password)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    access_token = create_access_token(user.id, user.email)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": UserResponse.model_validate(user),
    }
