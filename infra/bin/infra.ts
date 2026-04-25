#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { CDK_ENV } from '../config/environments';
import { GigaMcpServerStack } from '../lib/giga-mcp-server-stack';

const app = new cdk.App();

new GigaMcpServerStack(app, 'GigaMcpServer', {
  env: CDK_ENV,
  description: 'App Runner-based giga-mcp-server deployments — one per JIRA board',
});

app.synth();
