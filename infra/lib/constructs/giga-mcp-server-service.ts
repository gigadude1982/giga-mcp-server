import * as cdk from 'aws-cdk-lib';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as elb from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as route53Targets from 'aws-cdk-lib/aws-route53-targets';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';

export interface GigaMcpServerServiceProps {
  /** Short slug used in resource names and SSM paths, e.g. 'pitchvault-react', 'gigacorp-react' */
  boardId: string;
  /** MCP server name shown in Claude Desktop and get_server_info; defaults to boardId */
  serverName?: string;
  jiraProjectKey: string;
  jiraUrl: string;
  jiraUsername: string;
  /** GitHub repository in owner/repo format */
  githubRepo: string;
  /** Branch to use as the base for PRs; defaults to 'main' */
  githubBaseBranch?: string;
  /** Full subdomain that will be the public endpoint, e.g. 'mcp.pitchvault.co' */
  subdomain: string;
  /**
   * The hosted zone that owns the apex domain of `subdomain`.
   * For gigacorp boards this is the gigacorp.co zone; for pitchvault boards
   * it is the pitchvault.co zone.
   */
  hostedZone: route53.IHostedZone;
  ecrRepo: ecr.IRepository;
  /** Container image tag to deploy; defaults to 'latest' */
  imageTag?: string;
  /**
   * IAM role assumed by the running ECS task.  This role must allow the task
   * to call SSM GetParameter for the paths under /giga-mcp-server/{boardId}/.
   */
  taskRole: iam.IRole;
  /**
   * When set, CDK imports this existing Cognito user pool instead of creating
   * a new one.  Use this for the gigacorp board to preserve the manually-created
   * pool.  Omit for new boards — CDK will create a dedicated pool.
   */
  existingCognitoUserPoolId?: string;
  /** Fargate vCPU units; defaults to 256 (0.25 vCPU) */
  cpu?: number;
  /** Fargate memory in MiB; defaults to 512 */
  memory?: number;
}

/**
 * Reusable construct that provisions a single giga-mcp-server deployment for
 * one JIRA board.
 *
 * Resources created per instance:
 *   - Cognito user pool + app client (or imported if existingCognitoUserPoolId is set)
 *   - VPC (dedicated, /16 with 2 AZs)
 *   - ECS cluster + Fargate task definition + service
 *   - Application Load Balancer with an HTTPS listener
 *   - ACM certificate (DNS-validated against the provided hosted zone)
 *   - Route 53 A alias record → ALB
 *   - CloudWatch log group
 *   - SSM SecureString references for secrets
 *     (parameters must be created manually before deploy — see comments below)
 *
 * Secret migration note:
 *   Before running `cdk deploy`, create the three SSM SecureString parameters
 *   for each board.  For the gigacorp board the values come from the existing
 *   App Runner environment variables; for new boards supply fresh credentials.
 *
 *     aws ssm put-parameter \
 *       --name /giga-mcp-server/<boardId>/jira-api-token \
 *       --type SecureString --value "<token>"
 *     aws ssm put-parameter \
 *       --name /giga-mcp-server/<boardId>/anthropic-api-key \
 *       --type SecureString --value "<key>"
 *     aws ssm put-parameter \
 *       --name /giga-mcp-server/<boardId>/github-token \
 *       --type SecureString --value "<token>"
 */
export class GigaMcpServerService extends Construct {
  public readonly service: ecs.FargateService;
  public readonly loadBalancer: elb.ApplicationLoadBalancer;
  public readonly endpointUrl: string;

  constructor(scope: Construct, id: string, props: GigaMcpServerServiceProps) {
    super(scope, id);

    const {
      boardId,
      serverName = boardId,
      jiraProjectKey,
      jiraUrl,
      jiraUsername,
      githubRepo,
      githubBaseBranch = 'main',
      subdomain,
      hostedZone,
      ecrRepo,
      imageTag = 'latest',
      taskRole,
      existingCognitoUserPoolId,
      cpu = 256,
      memory = 512,
    } = props;

    const prefix = `giga-mcp-${boardId}`;

    // ── SSM SecureString references ──────────────────────────────────────────
    // Parameters are NOT created by CDK (CDK cannot create SecureString params).
    // Create them manually before running cdk deploy:
    //
    //   aws ssm put-parameter \
    //     --name /giga-mcp-server/<boardId>/jira-api-token \
    //     --type SecureString --value "<real value>"
    //
    // Repeat for anthropic-api-key and github-token.
    const jiraApiTokenParam = ssm.StringParameter.fromSecureStringParameterAttributes(
      this,
      'JiraApiTokenParam',
      { parameterName: `/giga-mcp-server/${boardId}/jira-api-token` },
    );

    const anthropicApiKeyParam = ssm.StringParameter.fromSecureStringParameterAttributes(
      this,
      'AnthropicApiKeyParam',
      { parameterName: `/giga-mcp-server/${boardId}/anthropic-api-key` },
    );

    const githubTokenParam = ssm.StringParameter.fromSecureStringParameterAttributes(
      this,
      'GithubTokenParam',
      { parameterName: `/giga-mcp-server/${boardId}/github-token` },
    );

    // ── Cognito user pool ────────────────────────────────────────────────────
    // Each board gets its own pool for full isolation.  The gigacorp board
    // imports the manually-created pool; all other boards get a new one.
    const userPool = existingCognitoUserPoolId
      ? cognito.UserPool.fromUserPoolId(this, 'UserPool', existingCognitoUserPoolId)
      : new cognito.UserPool(this, 'UserPool', {
          userPoolName: `${prefix}-users`,
          selfSignUpEnabled: false,
          signInAliases: { email: true },
          passwordPolicy: {
            minLength: 12,
            requireUppercase: true,
            requireLowercase: true,
            requireDigits: true,
            requireSymbols: false,
          },
          removalPolicy: cdk.RemovalPolicy.RETAIN,
        });

    const userPoolClient = userPool.addClient('AppClient', {
      userPoolClientName: `${prefix}-client`,
      authFlows: { userPassword: true, userSrp: true },
      generateSecret: false,
    });

    new cdk.CfnOutput(this, 'CognitoUserPoolId', {
      description: `Cognito user pool ID for the ${boardId} board`,
      value: userPool.userPoolId,
    });

    new cdk.CfnOutput(this, 'CognitoAppClientId', {
      description: `Cognito app client ID for the ${boardId} board`,
      value: userPoolClient.userPoolClientId,
    });

    // ── VPC ──────────────────────────────────────────────────────────────────
    const vpc = new ec2.Vpc(this, 'Vpc', {
      vpcName: `${prefix}-vpc`,
      maxAzs: 2,
      natGateways: 1,
    });

    // ── ECS cluster ──────────────────────────────────────────────────────────
    const cluster = new ecs.Cluster(this, 'Cluster', {
      clusterName: `${prefix}-cluster`,
      vpc,
      containerInsights: true,
    });

    // ── CloudWatch log group ─────────────────────────────────────────────────
    const logGroup = new logs.LogGroup(this, 'LogGroup', {
      logGroupName: `/ecs/${prefix}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // ── Task definition ──────────────────────────────────────────────────────
    const taskDefinition = new ecs.FargateTaskDefinition(
      this,
      'TaskDefinition',
      {
        family: prefix,
        cpu,
        memoryLimitMiB: memory,
        taskRole,
      },
    );

    // Grant the task role read access to the three SSM parameters.
    jiraApiTokenParam.grantRead(taskRole);
    anthropicApiKeyParam.grantRead(taskRole);
    githubTokenParam.grantRead(taskRole);

    // Grant ECR pull permissions to the task execution role.
    ecrRepo.grantPull(taskDefinition.obtainExecutionRole());

    const container = taskDefinition.addContainer('app', {
      image: ecs.ContainerImage.fromEcrRepository(ecrRepo, imageTag),
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: boardId,
        logGroup,
      }),
      environment: {
        GIGA_SERVER_NAME: serverName,
        GIGA_TRANSPORT: 'streamable-http',
        GIGA_HOST: '0.0.0.0',
        GIGA_PORT: '8000',
        GIGA_JIRA_URL: jiraUrl,
        GIGA_JIRA_USERNAME: jiraUsername,
        GIGA_JIRA_PROJECT_KEY: jiraProjectKey,
        GIGA_GITHUB_REPO: githubRepo,
        GIGA_GITHUB_BASE_BRANCH: githubBaseBranch,
        GIGA_COGNITO_USER_POOL_ID: userPool.userPoolId,
        GIGA_COGNITO_CLIENT_ID: userPoolClient.userPoolClientId,
      },
      secrets: {
        GIGA_JIRA_API_TOKEN: ecs.Secret.fromSsmParameter(jiraApiTokenParam),
        GIGA_ANTHROPIC_API_KEY: ecs.Secret.fromSsmParameter(anthropicApiKeyParam),
        GIGA_GITHUB_TOKEN: ecs.Secret.fromSsmParameter(githubTokenParam),
      },
      portMappings: [{ containerPort: 8000 }],
    });

    // ── Security groups ──────────────────────────────────────────────────────
    const albSg = new ec2.SecurityGroup(this, 'AlbSg', {
      vpc,
      securityGroupName: `${prefix}-alb-sg`,
      description: `ALB security group for ${prefix}`,
    });
    albSg.addIngressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(443), 'HTTPS from internet');
    albSg.addIngressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(80), 'HTTP redirect from internet');

    const serviceSg = new ec2.SecurityGroup(this, 'ServiceSg', {
      vpc,
      securityGroupName: `${prefix}-service-sg`,
      description: `ECS service security group for ${prefix}`,
    });
    serviceSg.addIngressRule(albSg, ec2.Port.tcp(8000), 'Traffic from ALB');

    // ── ALB ──────────────────────────────────────────────────────────────────
    this.loadBalancer = new elb.ApplicationLoadBalancer(this, 'Alb', {
      loadBalancerName: `${prefix}-alb`,
      vpc,
      internetFacing: true,
      securityGroup: albSg,
    });

    // ACM certificate — DNS-validated using the provided hosted zone.
    const certificate = new acm.Certificate(this, 'Certificate', {
      domainName: subdomain,
      validation: acm.CertificateValidation.fromDns(hostedZone),
    });

    // HTTPS listener
    const httpsListener = this.loadBalancer.addListener('HttpsListener', {
      port: 443,
      certificates: [certificate],
      defaultAction: elb.ListenerAction.fixedResponse(503, {
        messageBody: 'No targets registered',
      }),
    });

    // HTTP → HTTPS redirect
    this.loadBalancer.addListener('HttpListener', {
      port: 80,
      defaultAction: elb.ListenerAction.redirect({
        protocol: 'HTTPS',
        port: '443',
        permanent: true,
      }),
    });

    // ── ECS Fargate service ──────────────────────────────────────────────────
    this.service = new ecs.FargateService(this, 'Service', {
      serviceName: prefix,
      cluster,
      taskDefinition,
      desiredCount: 1,
      securityGroups: [serviceSg],
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      circuitBreaker: { rollback: true },
    });

    // Register ECS service as a target behind the HTTPS listener.
    httpsListener.addTargets('ServiceTarget', {
      targetGroupName: `${prefix}-tg`,
      port: 8000,
      protocol: elb.ApplicationProtocol.HTTP,
      targets: [this.service],
      healthCheck: {
        path: '/health',
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
        healthyHttpCodes: '200',
      },
      deregistrationDelay: cdk.Duration.seconds(30),
    });

    // ── Route 53 alias record ─────────────────────────────────────────────────
    new route53.ARecord(this, 'AliasRecord', {
      zone: hostedZone,
      recordName: subdomain,
      target: route53.RecordTarget.fromAlias(
        new route53Targets.LoadBalancerTarget(this.loadBalancer),
      ),
    });

    this.endpointUrl = `https://${subdomain}`;

    // ── Outputs ───────────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'EndpointUrl', {
      description: `Public endpoint for the ${boardId} giga-mcp-server deployment`,
      value: this.endpointUrl,
    });

    new cdk.CfnOutput(this, 'AlbDnsName', {
      description: `ALB DNS name for ${boardId}`,
      value: this.loadBalancer.loadBalancerDnsName,
    });

    // Suppress unused variable warning — container is referenced for side effects only.
    void container;
  }
}
