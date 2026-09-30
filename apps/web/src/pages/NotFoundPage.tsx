import { Link } from 'react-router-dom';
import { Page } from '../components/Shell';
import { Button, Card } from '../components/UI';

export default function NotFoundPage() {
  return <Page eyebrow="404" title="This page moved." subtitle="The classroom is still safe. Let's take you back to your workspace."><Card><Link to="/"><Button>Return to BhashaSaathi</Button></Link></Card></Page>;
}
