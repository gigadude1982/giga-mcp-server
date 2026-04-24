import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { BOARDS } from '../config/boards';
import { COGNITO_USER_POOL_ID } from '../config/environments';
import { SharedStack } from './shared-stack';
import { GigaMcpServerService } from './constructs/giga-mcp-server-service';

export interface ComputeStackProps extends cdk.StackProps {
  shared: SharedStack;
}

/**
 * Provisions one GigaMcpServerService construct per entry in config/boards.ts.
 *
 * ECS Express Mode note:
 *   ECS Express Mode is not yet available as a stable CDK L2 construct (as of
 *   mid-2025).  This stack uses standard Fargate.  When ECS Express Mode L2
 *   constructs become available, replace the FargateService inside
 *   GigaMcpServerService with the Express Mode equivalent — the surrounding
 *   plumbing (ALB, ACM, Route 53, SSM) does not need to change.
 */
export class ComputeStack extends cdk.Stack {
  public readonly services: Record<string, GigaMcpServerService> = {};

  constructor(scope: Construct, id: string, props: ComputeStackProps) {
    super(scope, id, props);

    const { shared } = props;

    for (const board of BOARDS) {
      const hostedZone =
        shared.hostedZones[board.boardId] ?? shared.gigacorpHostedZone;

      const service = new GigaMcpServerService(
        this,
        `GigaMcpService-${board.boardId}`,
        {
          boardId: board.boardId,
          jiraProjectKey: board.jiraProjectKey,
          jiraUrl: board.jiraUrl,
          jiraUsername: board.jiraUsername,
          githubRepo: board.githubRepo,
          githubBaseBranch: board.githubBaseBranch,
          subdomain: board.subdomain,
          hostedZone,
          ecrRepo: shared.ecrRepository,
          taskRole: shared.taskRole,
          // Import the existing pool for gigacorp; new boards get their own pool.
          existingCognitoUserPoolId:
            board.boardId === 'gigacorp' ? COGNITO_USER_POOL_ID : undefined,
        },
      );

      this.services[board.boardId] = service;
    }
  }
}
