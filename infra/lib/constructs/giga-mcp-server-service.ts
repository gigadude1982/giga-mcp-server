import * as cdk from 'aws-cdk-lib';
import * as apprunner from 'aws-cdk-lib/aws-apprunner';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import { Construct } from 'constructs';

export interface GigaMcpServerServiceProps {
  /** Short slug used in resource names and SSM paths, e.g. 'pitchvault-react' */
  boardId: string;
  /** MCP server name shown in Claude Desktop and get_server_info */
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
  ecrRepo: ecr.IRepository;
  /** Container image tag to deploy; defaults to 'latest' */
  imageTag?: string;
  /** App Runner instance role ARN (read SSM secrets). */
  instanceRoleArn: string;
  /** App Runner access role ARN (pull from ECR). */
  accessRoleArn: string;
  /**
   * When set, CDK imports this existing Cognito user pool instead of creating
   * a new one.  Use this for the gigacorp board to preserve the manually-created
   * pool.  Omit for new boards — CDK will create a dedicated pool.
   */
  existingCognitoUserPoolId?: string;
  /**
   * Optional suffix appended to the created pool's userPoolName. Changing it
   * forces CloudFormation to REPLACE the pool — used to recreate a pool whose
   * physical resource was deleted out-of-band (drift). Leave unset to keep the
   * existing pool untouched.
   */
  userPoolNameSuffix?: string;
  /** Wire Cognito pool/client IDs into the container env vars to enable JWT auth. */
  enableAuth?: boolean;
  /** Enable Pinecone integrated-inference vector store for semantic duplicate detection. */
  vectorEnabled?: boolean;
  /** Pinecone index name (must be created with an embedded model); defaults to 'giga-tickets'. */
  pineconeIndexName?: string;
  /** Enable code-history long-term memory store (queried by Implementer + Validator). Requires vectorEnabled. */
  codeHistoryEnabled?: boolean;
  /** Code-history Pinecone index name; defaults to 'giga-codehistory'. */
  pineconeCodehistoryIndexName?: string;
  /**
   * Enable the claude.ai / mobile OAuth connector: provisions a Cognito hosted-UI
   * domain and adds the authorization-code + PKCE grant to the app client. The
   * server serves its own OAuth authorization-server metadata. Requires enableAuth.
   */
  oauthConnectorEnabled?: boolean;
  /** App Runner CPU units; defaults to '256' (0.25 vCPU). */
  cpu?: string;
  /** App Runner memory in MB; defaults to '512' (0.5 GB). */
  memory?: string;
}

/**
 * Provisions a single giga-mcp-server App Runner deployment for one JIRA board.
 *
 * Per-board resources:
 *   - Cognito user pool + app client (or imported if existingCognitoUserPoolId is set)
 *   - App Runner service (image from shared ECR, env vars, SSM secret refs)
 *   - App Runner custom domain association for the subdomain
 *
 * Manual one-time setup per board:
 *   1. Create three SSM SecureString parameters BEFORE deploy:
 *        /giga-mcp-server/<boardId>/jira-api-token
 *        /giga-mcp-server/<boardId>/anthropic-api-key
 *        /giga-mcp-server/<boardId>/github-token
 *      Use scripts/setup-ssm.sh.
 *
 *   2. After deploy, App Runner returns three certificate validation CNAMEs in the
 *      stack output `<boardId>CertificateValidationRecords`.  Add those to the apex
 *      domain's DNS zone (Route 53 or Porkbun).  Once App Runner verifies the cert,
 *      add the final CNAME `mcp.<domain>` → `<boardId>DefaultUrl` to point traffic.
 */
export class GigaMcpServerService extends Construct {
  public readonly service: apprunner.CfnService;
  public readonly userPool: cognito.IUserPool;
  public readonly userPoolClient: cognito.IUserPoolClient;
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
      ecrRepo,
      imageTag = 'latest',
      instanceRoleArn,
      accessRoleArn,
      existingCognitoUserPoolId,
      userPoolNameSuffix = '',
      enableAuth = false,
      vectorEnabled = false,
      pineconeIndexName = 'giga-tickets',
      codeHistoryEnabled = false,
      pineconeCodehistoryIndexName = 'giga-codehistory',
      oauthConnectorEnabled = false,
      cpu = '256',
      memory = '512',
    } = props;

    if (codeHistoryEnabled && !vectorEnabled) {
      throw new Error('codeHistoryEnabled requires vectorEnabled because it reuses the Pinecone API key.');
    }

    if (oauthConnectorEnabled && !enableAuth) {
      throw new Error('oauthConnectorEnabled requires enableAuth (the OAuth flow needs the Cognito pool + client).');
    }

    const prefix = `giga-mcp-${boardId}`;
    const stack = cdk.Stack.of(this);

    // Cognito hosted-UI domain (only created when the OAuth connector is enabled).
    // The domain prefix must be globally unique per region — change it if cdk
    // deploy reports the prefix is taken.
    const hostedUiBaseUrl = `https://${prefix}.auth.${stack.region}.amazoncognito.com`;
    const ssmParamArn = (name: string) =>
      `arn:aws:ssm:${stack.region}:${stack.account}:parameter/giga-mcp-server/${boardId}/${name}`;

    // ── Cognito ─────────────────────────────────────────────────────────────
    // A non-empty suffix changes BOTH the userPoolName and the construct's
    // logical ID, so CloudFormation creates a brand-new pool and removes the old
    // logical resource (RemovalPolicy.RETAIN makes it skip deleting the gone
    // physical) — an in-place UserPoolName update would fail on a deleted pool.
    const poolId = `UserPool${userPoolNameSuffix.replace(/[^A-Za-z0-9]/g, '')}`;
    this.userPool = existingCognitoUserPoolId
      ? cognito.UserPool.fromUserPoolId(this, poolId, existingCognitoUserPoolId)
      : new cognito.UserPool(this, poolId, {
          userPoolName: `${prefix}-users${userPoolNameSuffix}`,
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

    this.userPoolClient = this.userPool.addClient('AppClient', {
      userPoolClientName: `${prefix}-client`,
      authFlows: { userPassword: true, userSrp: true },
      generateSecret: false,
      // claude.ai / mobile connector: authorization-code + PKCE (S256). Public
      // client (no secret) — leave Claude's "Client Secret" field blank.
      ...(oauthConnectorEnabled
        ? {
            oAuth: {
              flows: { authorizationCodeGrant: true },
              scopes: [
                cognito.OAuthScope.OPENID,
                cognito.OAuthScope.PROFILE,
                cognito.OAuthScope.EMAIL,
              ],
              callbackUrls: ['https://claude.ai/api/mcp/auth_callback'],
            },
          }
        : {}),
    });

    if (oauthConnectorEnabled) {
      this.userPool.addDomain('HostedUiDomain', {
        cognitoDomain: { domainPrefix: prefix },
      });
    }

    // ── App Runner service ──────────────────────────────────────────────────
    this.service = new apprunner.CfnService(this, 'Service', {
      serviceName: prefix,
      sourceConfiguration: {
        autoDeploymentsEnabled: true,
        authenticationConfiguration: { accessRoleArn },
        imageRepository: {
          imageIdentifier: `${ecrRepo.repositoryUri}:${imageTag}`,
          imageRepositoryType: 'ECR',
          imageConfiguration: {
            port: '8000',
            runtimeEnvironmentVariables: [
              { name: 'GIGA_SERVER_NAME', value: serverName },
              { name: 'GIGA_TRANSPORT', value: 'streamable-http' },
              { name: 'GIGA_HOST', value: '0.0.0.0' },
              { name: 'GIGA_PORT', value: '8000' },
              { name: 'GIGA_JIRA_URL', value: jiraUrl },
              { name: 'GIGA_JIRA_USERNAME', value: jiraUsername },
              { name: 'GIGA_JIRA_PROJECT_KEY', value: jiraProjectKey },
              { name: 'GIGA_GITHUB_REPO', value: githubRepo },
              { name: 'GIGA_GITHUB_BASE_BRANCH', value: githubBaseBranch },
              ...(enableAuth ? [
                { name: 'GIGA_COGNITO_USER_POOL_ID', value: this.userPool.userPoolId },
                { name: 'GIGA_COGNITO_CLIENT_ID', value: this.userPoolClient.userPoolClientId },
                { name: 'GIGA_PUBLIC_URL', value: `https://${subdomain}` },
              ] : []),
              ...(vectorEnabled ? [
                { name: 'GIGA_VECTOR_ENABLED', value: 'true' },
                { name: 'GIGA_PINECONE_INDEX_NAME', value: pineconeIndexName },
              ] : []),
              ...(codeHistoryEnabled ? [
                { name: 'GIGA_CODEHISTORY_ENABLED', value: 'true' },
                { name: 'GIGA_PINECONE_CODEHISTORY_INDEX_NAME', value: pineconeCodehistoryIndexName },
              ] : []),
              ...(oauthConnectorEnabled ? [
                { name: 'GIGA_OAUTH_CONNECTOR_ENABLED', value: 'true' },
                { name: 'GIGA_COGNITO_HOSTED_UI_DOMAIN', value: hostedUiBaseUrl },
              ] : []),
            ],
            runtimeEnvironmentSecrets: [
              { name: 'GIGA_JIRA_API_TOKEN', value: ssmParamArn('jira-api-token') },
              { name: 'GIGA_ANTHROPIC_API_KEY', value: ssmParamArn('anthropic-api-key') },
              { name: 'GIGA_GITHUB_TOKEN', value: ssmParamArn('github-token') },
              ...(vectorEnabled ? [
                { name: 'GIGA_PINECONE_API_KEY', value: ssmParamArn('pinecone-api-key') },
              ] : []),
              ...(codeHistoryEnabled ? [
                // HMAC secret for verifying GitHub webhook deliveries that
                // auto-ingest merged PRs into the code-history store.
                { name: 'GIGA_GITHUB_WEBHOOK_SECRET', value: ssmParamArn('github-webhook-secret') },
              ] : []),
            ],
          },
        },
      },
      instanceConfiguration: {
        cpu,
        memory,
        instanceRoleArn,
      },
      healthCheckConfiguration: {
        protocol: 'TCP',
        interval: 10,
        timeout: 5,
        healthyThreshold: 1,
        unhealthyThreshold: 5,
      },
      networkConfiguration: {
        ingressConfiguration: { isPubliclyAccessible: true },
        egressConfiguration: { egressType: 'DEFAULT' },
      },
      tags: [
        { key: 'Project', value: 'giga-mcp-server' },
        { key: 'Board', value: boardId },
      ],
    });

    // Custom domain association is not a CloudFormation resource type — managed
    // post-deploy via scripts/migrate-gigacorp-domain.sh (phase: validation, cutover).

    this.endpointUrl = `https://${subdomain}`;

    // ── Outputs ─────────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'ServiceArn', {
      description: `App Runner service ARN for the ${boardId} board`,
      value: this.service.attrServiceArn,
    });

    new cdk.CfnOutput(this, 'DefaultUrl', {
      description: `App Runner default URL — use as CNAME target for ${subdomain} after domain association`,
      value: cdk.Fn.sub('https://${ServiceUrl}', {
        ServiceUrl: this.service.attrServiceUrl,
      }),
    });

    new cdk.CfnOutput(this, 'CognitoUserPoolId', {
      description: `Cognito user pool ID for the ${boardId} board`,
      value: this.userPool.userPoolId,
    });

    new cdk.CfnOutput(this, 'CognitoAppClientId', {
      description: `Cognito app client ID for the ${boardId} board`,
      value: this.userPoolClient.userPoolClientId,
    });

    if (oauthConnectorEnabled) {
      new cdk.CfnOutput(this, 'OAuthHostedUiDomain', {
        description: `Cognito hosted-UI base URL for the ${boardId} OAuth connector`,
        value: hostedUiBaseUrl,
      });
      new cdk.CfnOutput(this, 'OAuthConnectorUrl', {
        description: `Add as a claude.ai custom connector; paste CognitoAppClientId under Advanced settings, leave secret blank`,
        value: `${this.endpointUrl}/mcp`,
      });
    }
  }
}
