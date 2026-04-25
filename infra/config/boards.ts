export interface BoardConfig {
  /** Short slug used in resource names and SSM paths */
  boardId: string;
  jiraProjectKey: string;
  jiraUrl: string;
  jiraUsername: string;
  githubRepo: string;
  githubBaseBranch?: string;
  /** MCP server name shown in Claude Desktop and get_server_info; defaults to boardId */
  serverName?: string;
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
    boardId: 'gigacorp-react',
    serverName: 'gigacorp-mcp-server',
    jiraProjectKey: 'GIGA',
    jiraUrl: 'https://gigacorporation.atlassian.net',
    jiraUsername: 'admin@gigacorp.co',
    githubRepo: 'gigadude1982/gigacorp-react',
    githubBaseBranch: 'main',
    subdomain: 'mcp.gigacorp.co',
    createHostedZone: false, // gigacorp.co hosted zone already exists in Route 53
  },
  {
    boardId: 'pitchvault-react',
    serverName: 'pitchvault-mcp-server',
    jiraProjectKey: 'PIT',
    jiraUrl: 'https://pitchvault.atlassian.net',
    jiraUsername: 'admin@pitchvault.co',
    githubRepo: 'gigadude1982/pitchvault-react',
    githubBaseBranch: 'main',
    subdomain: 'mcp.pitchvault.co',
    createHostedZone: true, // pitchvault.co hosted zone does not yet exist — CDK will create it
  },
];
