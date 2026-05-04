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
    pineconeIndexName: "pitchvault-tickets",
  },
];
