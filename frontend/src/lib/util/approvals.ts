// Who may authorise what (contract §1.3 separation of duties), and how an action is named on its card.
import type { IdentityDoc, ProposalDoc, Role } from '$lib/api/types';

export interface Gate {
	canHold: boolean;
	canDecide: boolean;
	reason: string;
}

/**
 * Least privilege and no self-approval: a person can hold only for a role the action still needs, with the
 * permission for it, and never as the only approver of an action they asked for.
 */
export function approvalGate(p: ProposalDoc, userId: string | null, role: Role | null, permissions: string[]): Gate {
	const no = (reason: string): Gate => ({ canHold: false, canDecide: false, reason });
	if (p.status !== 'pending') return no('Already decided');
	if (!userId || !role) return no('Sign in to decide');
	if (role === 'auditor') return no('Read only');
	if (p.proposedByUserId && p.proposedByUserId === userId) return { canHold: false, canDecide: true, reason: 'You asked for this, so someone else has to authorise it' };
	const perm = role === 'executive' ? 'actions:approve_crown_jewel' : 'actions:approve';
	if (!permissions.includes(perm) || !p.requiredRoles.includes(role)) {
		const who = p.requiredRoles.map((r) => (r === 'executive' ? 'the CISO' : r === 'responder' ? 'a responder' : r)).join(' and ');
		return no(`Needs ${who}`);
	}
	if (p.approvals.some((a) => a.userId === userId)) return { canHold: false, canDecide: false, reason: 'You held · waiting for a second person' };
	if (p.approvals.some((a) => a.role === role)) return { canHold: false, canDecide: false, reason: 'Your role has already held · waiting for another role' };
	if (p.awaitingRoles && !p.awaitingRoles.includes(role)) return { canHold: false, canDecide: false, reason: 'Waiting for another role' };
	// The API computes canApprove for the caller; it is the final word when present.
	if (p.canApprove === false) return { canHold: false, canDecide: false, reason: 'Not yours to authorise' };
	return { canHold: true, canDecide: true, reason: '' };
}

function possessive(name: string): string {
	return name.endsWith('s') ? `${name}’` : `${name}’s`;
}

/** "Isolate mem-dispatch-01", "Turn off Joy’s account", "Withdraw the deploy key". */
export function actionTitle(p: ProposalDoc, people: IdentityDoc[] = []): string {
	const person = people.find((x) => x.username === p.target);
	const first = person && person.kind !== 'service' ? person.displayName.split(' ')[0]! : null;
	switch (p.kind) {
		case 'isolate_host':
			return `Isolate ${p.target}`;
		case 'release_host':
			return `Release ${p.target} from isolation`;
		case 'disable_account':
			return first ? `Turn off ${possessive(first)} account` : `Turn off ${p.target}`;
		case 'enable_account':
			return first ? `Turn ${possessive(first)} account back on` : `Turn ${p.target} back on`;
		case 'revoke_sessions':
			return first ? `Sign ${first} out everywhere` : `Sign ${p.target} out everywhere`;
		case 'reset_password':
			return first ? `Reset ${possessive(first)} password` : `Reset the password for ${p.target}`;
		case 'revoke_access_key':
			return 'Withdraw the leaked cloud access key';
		case 'block_ip':
			return `Block ${p.target}`;
		case 'quarantine_email':
			return 'Quarantine the email';
		default:
			return p.kindLabel;
	}
}
