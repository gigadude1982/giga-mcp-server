export const ACCOUNT = '138606625420';
export const REGION = 'us-east-1';

export const CDK_ENV = {
  account: ACCOUNT,
  region: REGION,
};

/** ARN of the existing IAM role used by the ECS task (execution + task role) */
export const TASK_ROLE_ARN = 'arn:aws:iam::138606625420:role/giga-mcp-server-instance';

/**
 * The access role was originally created for App Runner to pull from ECR.
 * It is imported here for reference; the ECS task execution role is TASK_ROLE_ARN above.
 * @deprecated Replace with a dedicated ECS task execution role if the existing role
 *   lacks the required ECS task execution permissions.
 */
export const APPRUNNER_ECR_ACCESS_ROLE_ARN =
  'arn:aws:iam::138606625420:role/giga-mcp-server-apprunner-ecr';

export const ECR_REPO_NAME = 'giga-mcp-server';

export const COGNITO_USER_POOL_ID = 'us-east-1_gOIZZz2Eg';
export const COGNITO_APP_CLIENT_ID = '1tha4f07avp83h62e6i5khu3rn';

export const GIGACORP_HOSTED_ZONE_NAME = 'gigacorp.co';
export const GIGACORP_HOSTED_ZONE_ID = 'Z08385601B5HCX1AG6EO1';
