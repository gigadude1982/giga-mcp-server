import * as cdk from 'aws-cdk-lib';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as route53 from 'aws-cdk-lib/aws-route53';
import { Construct } from 'constructs';
import {
  APPRUNNER_ECR_ACCESS_ROLE_ARN,
  GIGACORP_HOSTED_ZONE_ID,
  GIGACORP_HOSTED_ZONE_NAME,
  TASK_ROLE_ARN,
} from '../config/environments';
import { BOARDS } from '../config/boards';

export interface SharedStackProps extends cdk.StackProps {
  ecrRepository: ecr.IRepository;
}

export class SharedStack extends cdk.Stack {
  public readonly gigacorpHostedZone: route53.IHostedZone;
  /**
   * Hosted zones keyed by boardId.  The gigacorp zone is always present.
   * The pitchvault zone is present when createHostedZone is true (CDK creates
   * it) or false (CDK looks it up).  Boards that share the gigacorp.co apex
   * domain point at gigacorpHostedZone directly.
   */
  public readonly hostedZones: Record<string, route53.IHostedZone>;
  public readonly taskRole: iam.IRole;
  /** Original App Runner ECR access role — retained for reference. */
  public readonly appRunnerEcrAccessRole: iam.IRole;
  public readonly ecrRepository: ecr.IRepository;

  constructor(scope: Construct, id: string, props: SharedStackProps) {
    super(scope, id, props);

    this.ecrRepository = props.ecrRepository;

    // ── IAM roles ────────────────────────────────────────────────────────────
    // Import the existing ECS task/instance role.
    this.taskRole = iam.Role.fromRoleArn(
      this,
      'GigaMcpTaskRole',
      TASK_ROLE_ARN,
      { mutable: false },
    );

    // Import the original App Runner ECR access role for reference.
    this.appRunnerEcrAccessRole = iam.Role.fromRoleArn(
      this,
      'AppRunnerEcrAccessRole',
      APPRUNNER_ECR_ACCESS_ROLE_ARN,
      { mutable: false },
    );

    // ── Route 53 ─────────────────────────────────────────────────────────────
    // Import the existing gigacorp.co hosted zone.
    this.gigacorpHostedZone = route53.HostedZone.fromHostedZoneAttributes(
      this,
      'GigacorpHostedZone',
      {
        hostedZoneId: GIGACORP_HOSTED_ZONE_ID,
        zoneName: GIGACORP_HOSTED_ZONE_NAME,
      },
    );

    // Build the hostedZones map.  For each board that has createHostedZone set
    // to true we create a new public hosted zone; otherwise we look it up.
    this.hostedZones = {
      gigacorp: this.gigacorpHostedZone,
    };

    for (const board of BOARDS) {
      if (board.boardId === 'gigacorp') {
        // Already handled above.
        continue;
      }

      const apexDomain = this.apexDomainFrom(board.subdomain);

      if (board.createHostedZone) {
        const newZone = new route53.PublicHostedZone(
          this,
          `HostedZone-${board.boardId}`,
          { zoneName: apexDomain },
        );
        this.hostedZones[board.boardId] = newZone;

        // Emit the name servers so that they can be registered with the
        // domain registrar as a manual step.
        new cdk.CfnOutput(this, `NameServers-${board.boardId}`, {
          description: `Route 53 name servers for ${apexDomain} — register these with your domain registrar`,
          value: cdk.Fn.join(', ', newZone.hostedZoneNameServers!),
        });
      } else {
        this.hostedZones[board.boardId] = route53.HostedZone.fromLookup(
          this,
          `HostedZone-${board.boardId}`,
          { domainName: apexDomain },
        );
      }
    }
  }

  /** Extracts the apex (registrable) domain from a multi-label subdomain. */
  private apexDomainFrom(subdomain: string): string {
    const parts = subdomain.split('.');
    // Return the last two labels, e.g. pitchvault.co from mcp.pitchvault.co
    return parts.slice(-2).join('.');
  }
}
