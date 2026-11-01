# CD knowledge notes

Write down what an agent cannot guess about your deployments.

## Apps and environments

- Argo CD app names and which environment/cluster each controls.
- App-of-apps structure, if any.

## Revisions and images

- Does the Argo CD revision point at a config repo or the app repo?
- How a commit becomes an image tag (tag format, registry).

## Kubernetes

- Context names, clusters and namespaces per environment.
- Label selectors that identify each service's pods.

## Known issues

- Apps that flap, long syncs, health-check quirks.
