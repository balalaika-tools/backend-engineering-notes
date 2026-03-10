# User Pool — Deep Dive

## Creating a User Pool

A User Pool is configured once and rarely changed (some settings are immutable after creation — read the notes below carefully).

```python
import boto3
import json

cognito = boto3.client('cognito-idp', region_name='us-east-1')

response = cognito.create_user_pool(
    PoolName='myapp-users',

    # How users sign in — IMMUTABLE after creation
    UsernameAttributes=['email'],           # users log in with email (not a separate username)
    # Alternative: don't set this if you want a username + optionally allow email as alias:
    # AliasAttributes=['email', 'phone_number', 'preferred_username']

    # Case sensitivity for usernames — IMMUTABLE
    UsernameConfiguration={'CaseSensitive': False},

    # Password rules
    Policies={
        'PasswordPolicy': {
            'MinimumLength': 12,
            'RequireUppercase': True,
            'RequireLowercase': True,
            'RequireNumbers': True,
            'RequireSymbols': True,
            'TemporaryPasswordValidityDays': 7,
        }
    },

    # Auto-verify email (sends verification code on sign-up)
    AutoVerifiedAttributes=['email'],

    # MFA — IMMUTABLE
    MfaConfiguration='OPTIONAL',           # 'OFF' | 'OPTIONAL' | 'ON'
    EnabledMfas=['SOFTWARE_TOKEN_MFA'],    # TOTP apps like Google Authenticator

    # Account recovery
    AccountRecoverySetting={
        'RecoveryMechanisms': [
            {'Priority': 1, 'Name': 'verified_email'},
        ]
    },

    # User attribute schema — attributes added here are PERMANENT (can't be removed)
    Schema=[
        {
            'Name': 'email',
            'AttributeDataType': 'String',
            'Required': True,
            'Mutable': False,
        },
        {
            'Name': 'department',           # will be accessible as custom:department
            'AttributeDataType': 'String',
            'Mutable': True,
            'StringAttributeConstraints': {'MinLength': '1', 'MaxLength': '100'},
        },
        {
            'Name': 'account_tier',
            'AttributeDataType': 'String',  # 'String' | 'Number' | 'DateTime' | 'Boolean'
            'Mutable': True,
        },
    ],

    # Email configuration (use SES for production — default has low send limits)
    EmailConfiguration={
        'EmailSendingAccount': 'DEVELOPER',                         # use your SES
        'SourceArn': 'arn:aws:ses:us-east-1:123456789012:identity/noreply@yourapp.com',
        'From': 'noreply@yourapp.com',
    },

    # Email verification content
    VerificationMessageTemplate={
        'DefaultEmailOption': 'CONFIRM_WITH_CODE',
        'EmailSubject': 'Your verification code',
        'EmailMessage': 'Your verification code is {####}',
    },
)

pool_id = response['UserPool']['Id']
```

### Immutable Settings (decide before creating)
- `UsernameAttributes` or `AliasAttributes` — how users sign in
- `UsernameConfiguration.CaseSensitive`
- `MfaConfiguration`
- Required attributes in `Schema`
- Custom attributes can be added later but **never removed**

---

## App Clients

### Creating an App Client

```python
response = cognito.create_user_pool_client(
    UserPoolId=pool_id,
    ClientName='web-app',

    # Server-side apps: set True and store the secret securely
    # Browser/mobile apps: ALWAYS False — secret would be exposed
    GenerateSecret=False,

    # Which auth flows this client supports
    ExplicitAuthFlows=[
        'ALLOW_USER_SRP_AUTH',          # secure password auth (recommended)
        'ALLOW_REFRESH_TOKEN_AUTH',     # required for token refresh
        # 'ALLOW_USER_PASSWORD_AUTH',   # plain password — only add if needed (server-side)
        # 'ALLOW_CUSTOM_AUTH',          # custom Lambda-driven auth flows
    ],

    # Token lifetimes
    AccessTokenValidity=60,
    IdTokenValidity=60,
    RefreshTokenValidity=30,
    TokenValidityUnits={
        'AccessToken': 'minutes',
        'IdToken': 'minutes',
        'RefreshToken': 'days',
    },

    # Which user attributes this app can read/write
    # Omit to grant access to all attributes (default for new clients)
    ReadAttributes=['email', 'name', 'custom:department'],
    WriteAttributes=['name', 'custom:department'],

    # OAuth 2.0 settings (needed if using hosted UI / authorization code flow)
    AllowedOAuthFlows=['code'],                         # authorization code flow
    AllowedOAuthScopes=['openid', 'email', 'profile'],
    AllowedOAuthFlowsUserPoolClient=True,
    CallbackURLs=['https://yourapp.com/callback'],
    LogoutURLs=['https://yourapp.com/logout'],
    SupportedIdentityProviders=['COGNITO'],             # add 'Google', 'Facebook' etc.

    # Prevent user existence errors (don't reveal whether email exists)
    PreventUserExistenceErrors='ENABLED',

    # Auth session duration for multi-step flows (in minutes, default 3)
    AuthSessionValidity=3,
)

client_id     = response['UserPoolClient']['ClientId']
client_secret = response['UserPoolClient'].get('ClientSecret')  # None if GenerateSecret=False
```

### Multiple Clients — Common Pattern

```python
# Web app — social login, code flow, short tokens
web_client = cognito.create_user_pool_client(
    UserPoolId=pool_id,
    ClientName='web-app',
    GenerateSecret=False,
    ExplicitAuthFlows=['ALLOW_USER_SRP_AUTH', 'ALLOW_REFRESH_TOKEN_AUTH'],
    AllowedOAuthFlows=['code'],
    AllowedOAuthScopes=['openid', 'email', 'profile'],
    AllowedOAuthFlowsUserPoolClient=True,
    CallbackURLs=['https://yourapp.com/callback'],
    SupportedIdentityProviders=['COGNITO', 'Google'],
    AccessTokenValidity=60,
    RefreshTokenValidity=30,
    TokenValidityUnits={'AccessToken': 'minutes', 'RefreshToken': 'days'},
)

# Mobile app — SRP only, longer refresh token
mobile_client = cognito.create_user_pool_client(
    UserPoolId=pool_id,
    ClientName='mobile-app',
    GenerateSecret=False,
    ExplicitAuthFlows=['ALLOW_USER_SRP_AUTH', 'ALLOW_REFRESH_TOKEN_AUTH'],
    AccessTokenValidity=60,
    RefreshTokenValidity=90,    # 90 days — mobile apps stay logged in longer
    TokenValidityUnits={'AccessToken': 'minutes', 'RefreshToken': 'days'},
)

# Backend service — client credentials (machine-to-machine, no user login)
server_client = cognito.create_user_pool_client(
    UserPoolId=pool_id,
    ClientName='backend-service',
    GenerateSecret=True,        # server-side only, secret is safe here
    ExplicitAuthFlows=[],       # no user auth flows needed
    AllowedOAuthFlows=['client_credentials'],
    AllowedOAuthScopes=['myapi/read', 'myapi/write'],
    AllowedOAuthFlowsUserPoolClient=True,
)
```

---

## Authentication Flows

### SRP (Secure Remote Password) — Recommended

The password never travels over the wire in plaintext. The client and server do a cryptographic handshake to prove the client knows the password without sending it. Use the `warrant` or `boto3-cognito-auth` library which handles the SRP math.

```
App → Cognito: InitiateAuth (AuthFlow=USER_SRP_AUTH, USERNAME, SRP_A value)
Cognito → App: Challenge (PASSWORD_VERIFIER) with SRP_B, salt, secret block
App → Cognito: RespondToAuthChallenge with PASSWORD_CLAIM_SIGNATURE
Cognito → App: Tokens (if no MFA) OR next challenge (e.g. SMS_MFA)
```

### USER_PASSWORD_AUTH — Simple

Password sent in plaintext over HTTPS. Fine for server-side code, not recommended for client-side.

```python
response = cognito.initiate_auth(
    ClientId=client_id,
    AuthFlow='USER_PASSWORD_AUTH',
    AuthParameters={
        'USERNAME': 'jane@example.com',
        'PASSWORD': 'MyPass123!',
    },
)

# Could return tokens immediately, or a challenge (e.g. MFA)
if 'AuthenticationResult' in response:
    tokens = response['AuthenticationResult']
elif 'ChallengeName' in response:
    # Handle MFA or NEW_PASSWORD_REQUIRED challenge
    handle_challenge(response)
```

### Handling Challenges

After `initiate_auth`, Cognito might not give you tokens immediately. It issues a challenge:

```python
def authenticate(username, password, client_id):
    response = cognito.initiate_auth(
        ClientId=client_id,
        AuthFlow='USER_PASSWORD_AUTH',
        AuthParameters={'USERNAME': username, 'PASSWORD': password},
    )

    # Keep responding until we get tokens
    while 'ChallengeName' in response:
        challenge = response['ChallengeName']
        session = response['Session']

        if challenge == 'NEW_PASSWORD_REQUIRED':
            new_password = input("Set a new password: ")
            response = cognito.respond_to_auth_challenge(
                ClientId=client_id,
                ChallengeName='NEW_PASSWORD_REQUIRED',
                Session=session,
                ChallengeResponses={
                    'USERNAME': username,
                    'NEW_PASSWORD': new_password,
                },
            )

        elif challenge == 'SMS_MFA':
            code = input("Enter SMS MFA code: ")
            response = cognito.respond_to_auth_challenge(
                ClientId=client_id,
                ChallengeName='SMS_MFA',
                Session=session,
                ChallengeResponses={
                    'USERNAME': username,
                    'SMS_MFA_CODE': code,
                },
            )

        elif challenge == 'SOFTWARE_TOKEN_MFA':
            code = input("Enter TOTP code: ")
            response = cognito.respond_to_auth_challenge(
                ClientId=client_id,
                ChallengeName='SOFTWARE_TOKEN_MFA',
                Session=session,
                ChallengeResponses={
                    'USERNAME': username,
                    'SOFTWARE_TOKEN_MFA_CODE': code,
                },
            )

    return response['AuthenticationResult']
```

### Common Challenge Names

| Challenge | Means |
|-----------|-------|
| `NEW_PASSWORD_REQUIRED` | Admin created the user — they must set a new password |
| `SMS_MFA` | User has SMS MFA enabled — needs to enter SMS code |
| `SOFTWARE_TOKEN_MFA` | User has TOTP (Google Authenticator) — needs to enter code |
| `MFA_SETUP` | MFA is required but not yet configured for this user |
| `CUSTOM_CHALLENGE` | Lambda-driven custom auth challenge |

---

## User Management

### Self Sign-Up

```python
# User signs themselves up
response = cognito.sign_up(
    ClientId=client_id,
    Username='jane@example.com',
    Password='MyPass123!',
    UserAttributes=[
        {'Name': 'email', 'Value': 'jane@example.com'},
        {'Name': 'name', 'Value': 'Jane Doe'},
        {'Name': 'custom:department', 'Value': 'engineering'},
    ],
)

user_sub = response['UserSub']           # the user's permanent UUID
confirmed = response['UserConfirmed']    # False — verification email sent

# User receives code → confirms
cognito.confirm_sign_up(
    ClientId=client_id,
    Username='jane@example.com',
    ConfirmationCode='123456',
)
```

### Admin Creates User

```python
# Admin provisions a user — no self-registration required
cognito.admin_create_user(
    UserPoolId=pool_id,
    Username='jane@example.com',
    UserAttributes=[
        {'Name': 'email', 'Value': 'jane@example.com'},
        {'Name': 'email_verified', 'Value': 'true'},
        {'Name': 'name', 'Value': 'Jane Doe'},
        {'Name': 'custom:department', 'Value': 'engineering'},
    ],
    TemporaryPassword='Temp1234!',
    MessageAction='SUPPRESS',           # don't send welcome email
)
# User status: FORCE_CHANGE_PASSWORD
# On first login, Cognito will issue NEW_PASSWORD_REQUIRED challenge

# Skip forced password change — set permanent immediately
cognito.admin_set_user_password(
    UserPoolId=pool_id,
    Username='jane@example.com',
    Password='Permanent1234!',
    Permanent=True,
)
# User status: CONFIRMED
```

### Get, Update, Disable, Delete

```python
# Get user
user = cognito.admin_get_user(UserPoolId=pool_id, Username='jane@example.com')
print(user['UserStatus'])               # CONFIRMED
print({a['Name']: a['Value'] for a in user['UserAttributes']})

# Update attributes
cognito.admin_update_user_attributes(
    UserPoolId=pool_id,
    Username='jane@example.com',
    UserAttributes=[
        {'Name': 'custom:department', 'Value': 'platform'},
    ],
)

# Disable login (user still exists, just can't sign in)
cognito.admin_disable_user(UserPoolId=pool_id, Username='jane@example.com')

# Re-enable
cognito.admin_enable_user(UserPoolId=pool_id, Username='jane@example.com')

# Delete user
cognito.admin_delete_user(UserPoolId=pool_id, Username='jane@example.com')

# Force user to sign out everywhere (invalidates all tokens)
cognito.admin_user_global_sign_out(UserPoolId=pool_id, Username='jane@example.com')
```

### List Users

```python
paginator = cognito.get_paginator('list_users')
for page in paginator.paginate(UserPoolId=pool_id):
    for user in page['Users']:
        attrs = {a['Name']: a['Value'] for a in user['UserAttributes']}
        print(user['Username'], user['UserStatus'], attrs.get('email'))

# Filter — find users by attribute
response = cognito.list_users(
    UserPoolId=pool_id,
    Filter='email = "jane@example.com"',    # exact match
    # Filter='email ^= "jane"',             # starts with
    # Filter='name = "Jane Doe"',
)
```

---

## Groups

Groups let you tag users with a role/tier. Membership is reflected in JWT tokens.

```python
# Create groups
cognito.create_group(
    UserPoolId=pool_id,
    GroupName='admins',
    Description='Admin users',
    Precedence=1,               # lower = higher priority when multiple groups conflict
)
cognito.create_group(UserPoolId=pool_id, GroupName='premium', Precedence=10)
cognito.create_group(UserPoolId=pool_id, GroupName='free',    Precedence=20)

# Add user to group
cognito.admin_add_user_to_group(
    UserPoolId=pool_id,
    Username='jane@example.com',
    GroupName='admins',
)

# Remove user from group
cognito.admin_remove_user_from_group(
    UserPoolId=pool_id,
    Username='jane@example.com',
    GroupName='admins',
)

# List users in a group
response = cognito.list_users_in_group(UserPoolId=pool_id, GroupName='admins')
for user in response['Users']:
    print(user['Username'])

# List groups a user belongs to
response = cognito.admin_list_groups_for_user(
    UserPoolId=pool_id,
    Username='jane@example.com',
)
for group in response['Groups']:
    print(group['GroupName'])
```

In the JWT, group membership appears as:
```json
{
  "cognito:groups": ["admins", "premium"],
  "sub": "uuid-here",
  "email": "jane@example.com"
}
```

---

## Lambda Triggers

Lambda triggers let you hook into Cognito's auth lifecycle to customize behavior.

| Trigger | When it fires | Common use |
|---------|--------------|-----------|
| **Pre sign-up** | Before a new user account is created | Validate email domain, auto-confirm, auto-verify |
| **Post confirmation** | After user confirms their account | Add user to default group, sync to your DB |
| **Pre authentication** | Before credentials are checked | Block certain users, log attempts |
| **Post authentication** | After successful login | Audit logging, last-login tracking |
| **Pre token generation** | Before tokens are issued | Add custom claims to JWT, modify group claims |
| **Custom message** | Before verification/welcome emails are sent | Customize email content and links |
| **User migration** | When a user doesn't exist in pool but tries to sign in | Migrate users from legacy auth system transparently |
| **Define auth challenge** | Start of a custom auth flow | Define what the next challenge is |
| **Create auth challenge** | Create the actual challenge | Generate OTP, captcha, etc. |
| **Verify auth challenge** | Validate the user's response | Check OTP, validate captcha |

### Example: Post Confirmation — Add User to Group and Sync to DB

```python
import boto3

def handler(event, context):
    """Fires after a user confirms their account."""
    user_pool_id = event['userPoolId']
    username = event['userName']
    email = event['request']['userAttributes'].get('email')

    # Add to default group
    cognito = boto3.client('cognito-idp')
    cognito.admin_add_user_to_group(
        UserPoolId=user_pool_id,
        Username=username,
        GroupName='free',
    )

    # Sync to your own database
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table('users')
    table.put_item(Item={
        'userId': event['request']['userAttributes']['sub'],
        'email': email,
        'tier': 'free',
        'createdAt': datetime.utcnow().isoformat(),
    })

    # Must return the event unchanged (Cognito requires this)
    return event
```

### Example: Pre Token Generation — Add Custom Claims

```python
def handler(event, context):
    """Add custom claims to JWT tokens before they're issued."""
    # Read user's tier from DynamoDB
    user_sub = event['request']['userAttributes']['sub']
    # ... fetch from DB ...

    event['response']['claimsOverrideDetails'] = {
        'claimsToAddOrOverride': {
            'app_tier': 'premium',
            'feature_flags': 'dark_mode,export,api_access',
        },
        'claimsToSuppress': [],  # remove claims from the token if needed
    }
    return event
```

Attach a trigger to the pool:
```python
cognito.update_user_pool(
    UserPoolId=pool_id,
    LambdaConfig={
        'PostConfirmation': 'arn:aws:lambda:us-east-1:123456789012:function:PostConfirmation',
        'PreTokenGeneration': 'arn:aws:lambda:us-east-1:123456789012:function:PreTokenGen',
    }
)
```

---

## Password Reset

```python
# User-initiated forgot password — sends code to email
cognito.forgot_password(
    ClientId=client_id,
    Username='jane@example.com',
)

# User enters code + new password
cognito.confirm_forgot_password(
    ClientId=client_id,
    Username='jane@example.com',
    ConfirmationCode='654321',
    Password='NewPass456!',
)

# Admin force-reset (user gets NEW_PASSWORD_REQUIRED on next login)
cognito.admin_reset_user_password(
    UserPoolId=pool_id,
    Username='jane@example.com',
)
```

---

## Token Refresh

```python
response = cognito.initiate_auth(
    ClientId=client_id,
    AuthFlow='REFRESH_TOKEN_AUTH',
    AuthParameters={'REFRESH_TOKEN': stored_refresh_token},
)

new_access_token = response['AuthenticationResult']['AccessToken']
new_id_token     = response['AuthenticationResult']['IdToken']
# Note: a new RefreshToken is NOT issued — same one stays valid
```

---

## User Pool Domain (for Hosted UI)

If you want Cognito to serve the login page for you (useful for prototyping or OAuth flows), you need to add a domain to the pool:

```python
# Custom AWS subdomain
cognito.create_user_pool_domain(
    Domain='myapp-auth',             # becomes: myapp-auth.auth.us-east-1.amazoncognito.com
    UserPoolId=pool_id,
)

# Or your own domain (requires ACM certificate)
cognito.create_user_pool_domain(
    Domain='auth.yourapp.com',
    UserPoolId=pool_id,
    CustomDomainConfig={
        'CertificateArn': 'arn:aws:acm:us-east-1:123456789012:certificate/...',
    }
)
```

OAuth Authorization Code flow URL (redirect user here to log in):
```
https://myapp-auth.auth.us-east-1.amazoncognito.com/oauth2/authorize
  ?response_type=code
  &client_id=<client_id>
  &redirect_uri=https://yourapp.com/callback
  &scope=openid+email+profile
```

---

## Related Docs

- [cognito.md](./cognito.md) — Mental model and overview
- [tokens.md](./tokens.md) — JWT structure, validation, refresh flow
