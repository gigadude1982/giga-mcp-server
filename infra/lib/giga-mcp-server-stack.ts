import * as cdk from 'aws-cdk-lib';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import { Construct } from 'constructs';
import { BOARDS } from '../config/boards';
import {
  ACCESS_ROLE_ARN,
  COGNITO_USER_POOL_ID,
  ECR_REPO_NAME,
  INSTANCE_ROLE_ARN,
} from '../config/environments';
import { GigaMcpServerService } from './constructs/giga-mcp-server-service';

/**
 * Single stack provisioning App Runner-based MCP server deployments for every
 * board in config/boards.ts.  Mirrors the existing manual setup at
 * mcp.gigacorp.co — same ECR image, same IAM roles, same shape of env vars +
 * SSM secrets — extended to N boards via the GigaMcpServerService construct.
 */
export class GigaMcpServerStack extends cdk.Stack {
  public readonly services: Record<string, GigaMcpServerService> = {};

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const ecrRepo = ecr.Repository.fromRepositoryName(this, 'EcrRepo', ECR_REPO_NAME);

    for (const board of BOARDS) {
      this.services[board.boardId] = new GigaMcpServerService(
        this,
        `Service-${board.boardId}`,
        {
          boardId: board.boardId,
          serverName: board.serverName,
          jiraProjectKey: board.jiraProjectKey,
          jiraUrl: board.jiraUrl,
          jiraUsername: board.jiraUsername,
          githubRepo: board.githubRepo,
          githubBaseBranch: board.githubBaseBranch,
          subdomain: board.subdomain,
          vectorEnabled: board.vectorEnabled,
          pineconeIndexName: board.pineconeIndexName,
          ecrRepo,
          accessRoleArn: ACCESS_ROLE_ARN,
          instanceRoleArn: INSTANCE_ROLE_ARN,
          existingCognitoUserPoolId:
            board.boardId === 'gigacorp-react' ? COGNITO_USER_POOL_ID : undefined,
          enableAuth: board.enableAuth,
        },
      );
    }
  }
}
