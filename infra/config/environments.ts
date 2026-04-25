export const ACCOUNT = '138606625420';
export const REGION = 'us-east-1';

export const CDK_ENV = {
  account: ACCOUNT,
  region: REGION,
};

/** Existing ECR repository — shared across all boards. */
export const ECR_REPO_NAME = 'giga-mcp-server';

/** App Runner instance role — task assumes this for SSM access. */
export const INSTANCE_ROLE_ARN =
  'arn:aws:iam::138606625420:role/giga-mcp-server-instance';

/** App Runner access role — used to pull images from ECR. */
export const ACCESS_ROLE_ARN =
  'arn:aws:iam::138606625420:role/giga-mcp-server-apprunner-ecr';

/** Existing Cognito user pool — imported by the gigacorp-react board. */
export const COGNITO_USER_POOL_ID = 'us-east-1_gOIZZz2Eg';
