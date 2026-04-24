#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { CDK_ENV } from '../config/environments';
import { EcrStack } from '../lib/ecr-stack';
import { SharedStack } from '../lib/shared-stack';
import { ComputeStack } from '../lib/compute-stack';

const app = new cdk.App();

// ── ECR ───────────────────────────────────────────────────────────────────────
// Imports the existing ECR repository and attaches lifecycle rules.
const ecrStack = new EcrStack(app, 'GigaMcpServerEcr', {
  env: CDK_ENV,
  description: 'ECR repository for giga-mcp-server container images',
});

// ── Shared resources ──────────────────────────────────────────────────────────
// Imports Cognito, IAM roles, and Route 53 hosted zones.
const sharedStack = new SharedStack(app, 'GigaMcpServerShared', {
  env: CDK_ENV,
  description: 'Shared resources: Cognito, IAM, Route 53 for giga-mcp-server',
  ecrRepository: ecrStack.repository,
});
sharedStack.addDependency(ecrStack);

// ── Compute ───────────────────────────────────────────────────────────────────
// One ECS Fargate service per board defined in config/boards.ts.
const computeStack = new ComputeStack(app, 'GigaMcpServerCompute', {
  env: CDK_ENV,
  description: 'ECS Fargate services for each giga-mcp-server board deployment',
  shared: sharedStack,
});
computeStack.addDependency(sharedStack);

app.synth();
