#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { CDK_ENV } from '../config/environments';
import { GigaMcpServerStack } from '../lib/giga-mcp-server-stack';

const app = new cdk.App();

const stackName = process.env.STACK_NAME ?? 'GigaMcpServer';

new GigaMcpServerStack(app, stackName, {
  env: CDK_ENV,
  description: 'App Runner-based giga-mcp-server deployments — one per JIRA board',
  stackName,
});

app.synth();
