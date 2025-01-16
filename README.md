# lambda-ebs-cleaner

## Install

```bash
$ brew install python@3.9

$ npm install -g serverless

$ sls plugin install -n serverless-python-requirements
$ sls plugin install -n serverless-dotenv-plugin

$ pip3 install --upgrade -r requirements.txt
```

## Environment Variables

```bash
$ cp .env.example .env
```

### Slack Webhook

```bash
RETENTION_DAYS="0"
SLACK_WEBHOOK_URL=""
```

## Deployment

In order to deploy the example, you need to run the following command:

```bash
$ sls deploy --region ap-northeast-2
```
