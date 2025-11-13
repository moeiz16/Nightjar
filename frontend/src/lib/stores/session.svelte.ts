/* eslint-disable svelte/prefer-svelte-reactivity -- local Date arithmetic, not reactive state */
import { api } from '$lib/api';
import type { Me, Role } from '$lib/api/types';
import { offsetFromDateHeader, setServerOffset } from '$lib/time/clock';

class Session {
	me = $state<Me | null>(null);
	loaded = $state(false);
	#pending: Promise<Me | null> | null = null;

	get role(): Role | null {
		return this.me?.membership?.role ?? null;
	}
	get roleLabel(): string {
		return this.me?.membership?.roleLabel ?? '';
	}
	get name(): string {
		return this.me?.user.fullName ?? '';
	}
	get firstName(): string {
		return this.name.split(' ')[0] ?? '';
	}
	get org() {
		return this.me?.activeOrg ?? null;
	}
	can(perm: string): boolean {
		return this.me?.membership?.permissions.includes(perm) ?? false;
	}
	/** Landing route after sign-in: executives land on the brief instead of tiles (DESIGN.md §3). */
	home(): string {
		return this.role === 'executive' ? '/brief' : '/watch';
	}
	set(me: Me | null) {
		this.me = me;
		this.loaded = true;
		if (me?.clock?.demo && me.clock.now) {
			// the server's demo clock, time of day included
			const off = offsetFromDateHeader(me.clock.now);
			if (off) setServerOffset(off);
		} else if (me?.clock?.demo && me.clock.today) {
			// keep the browser's time of day, but trust the server's calendar date for business logic
			const local = new Date();
			const today = local.toLocaleDateString('sv-SE');
			if (today !== me.clock.today) {
				const [y, m, d] = me.clock.today.split('-').map(Number);
				const shifted = new Date(local);
				shifted.setFullYear(y!, m! - 1, d!);
				setServerOffset(+shifted - +local);
			}
		}
	}
	load(force = false): Promise<Me | null> {
		if (this.loaded && !force) return Promise.resolve(this.me);
		this.#pending ??= api.auth
			.me()
			.then((me) => (this.set(me), me))
			.catch(() => (this.set(null), null))
			.finally(() => (this.#pending = null));
		return this.#pending;
	}
	async login(email: string, password: string): Promise<Me> {
		await api.auth.csrf().catch(() => undefined);
		const me = await api.auth.login(email, password);
		this.set(me);
		return me;
	}
	async logout() {
		await api.auth.logout().catch(() => undefined);
		this.set(null);
	}
}

export const session = new Session();
