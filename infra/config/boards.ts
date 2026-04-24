export interface BoardConfig {
  /** Short slug used in resource names and SSM paths */
  boardId: string;
  jiraProjectKey: string;
  jiraUrl: string;
  jiraUsername: string;
  githubRepo: string;
  githubBaseBranch?: string;
  /** Full subdomain that will be the public endpoint, e.g. mcp.pitchvault.co */
  subdomain: string;
  /**
   * When true a new Route 53 hosted zone will be created for the subdomain's
   * apex domain.  When false the hosted zone must already exist and will be
   * looked up by domain name.
   */
  createHostedZone: boolean;
}

export const BOARDS: BoardConfig[] = [
  {
    boardId: 'gigacorp',
    jiraProjectKey: 'GIGA',
    jiraUrl: 'https://gigacorporation.atlassian.net',
    jiraUsername: 'claude@gigacorp.co',
    githubRepo: 'gigadude1982/giga-mcp-server',
    githubBaseBranch: 'main',
    subdomain: 'mcp.gigacorp.co',
    createHostedZone: false, // gigacorp.co hosted zone already exists in Route 53
  },
  {
    boardId: 'pitchvault',
    jiraProjectKey: 'PIT',
    jiraUrl: 'https://pitchvault.atlassian.net',
    jiraUsername: 'claude@pitchvault.co',
    githubRepo: 'gigadude1982/pitchvault-react',
    githubBaseBranch: 'main',
    subdomain: 'mcp.pitchvault.co',
    createHostedZone: true, // pitchvault.co hosted zone does not yet exist — CDK will create it
  },
];
