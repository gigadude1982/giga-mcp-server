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
   * Enable Cognito JWT auth on the App Runner service.
   * Set to false during development/testing; flip to true when the full
   * OAuth authorization code flow is implemented so Claude apps can
   * auto-authenticate.
   */
  enableAuth?: boolean;
  /** Enable Pinecone vector store for semantic duplicate detection and ticket calibration. */
  vectorEnabled?: boolean;
  /** Pinecone index name; defaults to 'giga-tickets'. */
  pineconeIndexName?: string;
  /**
   * Enable the code-history long-term memory store (separate Pinecone index of
   * merged-PR summaries). When true, the Implementer + Validator agents query
   * this store at pipeline runtime to ground generation in prior PR patterns.
   * Requires vectorEnabled: true since it reuses the Pinecone API key.
   */
  codeHistoryEnabled?: boolean;
  /** Pinecone code-history index name; defaults to 'giga-codehistory'. */
  pineconeCodehistoryIndexName?: string;
  /**
   * Optional suffix on the Cognito pool name. Bump it to force CloudFormation
   * to REPLACE the pool — needed when the physical pool was deleted out-of-band
   * (drift) and CDK otherwise no-ops. Leave unset for healthy boards.
   */
  cognitoPoolSuffix?: string;
}

export const BOARDS: BoardConfig[] = [
  {
    boardId: "gigacorp-react",
    serverName: "gigacorp-mcp-server",
    jiraProjectKey: "GIGA",
    jiraUrl: "https://gigacorporation.atlassian.net",
    jiraUsername: "admin@gigacorp.co",
    githubRepo: "gigadude1982/gigacorp-react",
    githubBaseBranch: "main",
    subdomain: "mcp.gigacorp.co",
    enableAuth: true,
    vectorEnabled: true,
    pineconeIndexName: "gigacorp-tickets",
  },
  {
    boardId: "pitchvault-react",
    serverName: "pitchvault-mcp-server",
    jiraProjectKey: "PIT",
    jiraUrl: "https://pitchvault.atlassian.net",
    jiraUsername: "admin@pitchvault.co",
    githubRepo: "gigadude1982/pitchvault-react",
    githubBaseBranch: "main",
    subdomain: "mcp.pitchvault.co",
    enableAuth: true,
    pineconeIndexName: "pitchvault-tickets",
    cognitoPoolSuffix: "-v2",  // force replace: original pool was deleted out-of-band
  },
  {
    boardId: "punch-pwa",
    serverName: "punch-mcp-server",
    jiraProjectKey: "PUNCH",
    jiraUrl: "https://gigacorporation.atlassian.net",
    jiraUsername: "admin@gigacorp.co",
    githubRepo: "gigadude1982/punch-pwa",
    githubBaseBranch: "main",
    subdomain: "mcp.punch.gigacorp.co",
    enableAuth: true,
    vectorEnabled: true,
    pineconeIndexName: "punch-tickets",
    codeHistoryEnabled: true,
    pineconeCodehistoryIndexName: "punch-codehistory",
  },
];
