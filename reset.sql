UPDATE users SET password='$2b$12$vcm.f3OGJORLZ6/Ygs3MieVXVI714KnGQEYYiFZxa.XmGwRwYvta2' WHERE username='admin@catnip.com';

from passlib.context import CryptContext
ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
print(ctx.hash("Admin@123"))