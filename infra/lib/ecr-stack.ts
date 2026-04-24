import * as cdk from 'aws-cdk-lib';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import { Construct } from 'constructs';
import { ECR_REPO_NAME } from '../config/environments';

export class EcrStack extends cdk.Stack {
  public readonly repository: ecr.IRepository;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Import the existing ECR repository — do not recreate it.
    this.repository = ecr.Repository.fromRepositoryName(
      this,
      'GigaMcpServerRepo',
      ECR_REPO_NAME,
    );

    // Apply lifecycle rules via an escape-hatch CfnRepository overlay so that
    // we can add policy without replacing the imported resource.
    // The lifecycle policy below keeps the 10 most-recent tagged images and
    // expires all untagged images after 7 days.
    new ecr.CfnLifecyclePolicy(this, 'GigaMcpServerLifecyclePolicy', {
      repositoryName: ECR_REPO_NAME,
      lifecyclePolicyText: JSON.stringify({
        rules: [
          {
            rulePriority: 1,
            description: 'Keep last 10 tagged images',
            selection: {
              tagStatus: 'tagged',
              tagPrefixList: [''],
              countType: 'imageCountMoreThan',
              countNumber: 10,
            },
            action: { type: 'expire' },
          },
          {
            rulePriority: 2,
            description: 'Expire untagged images after 7 days',
            selection: {
              tagStatus: 'untagged',
              countType: 'sinceImagePushed',
              countUnit: 'days',
              countNumber: 7,
            },
            action: { type: 'expire' },
          },
        ],
      }),
    });
  }
}
