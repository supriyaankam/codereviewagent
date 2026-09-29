import {
  collection,
  doc,
  setDoc,
  getDoc,
  addDoc,
  updateDoc,
  deleteDoc,
  query,
  where,
  orderBy,
  getDocs,
  serverTimestamp,
} from 'firebase/firestore';
import { db } from './firebase';

// ─── Collection name constants ────────────────────────────────────────────────
const COLLECTIONS = {
  USERS:          'users',
  PROJECTS:       'projects',
  CODE_REVIEWS:   'code_reviews',
  REVIEW_ISSUES:  'review_issues',
  FEEDBACK:       'feedback',
  TEAM_STANDARDS: 'team_standards',
};

// ─── users ────────────────────────────────────────────────────────────────────

/**
 * Create or overwrite a user document in Firestore when a new account is registered.
 * @param {import('firebase/auth').User} user
 * @param {object} extraData
 */
export async function createUserDocument(user, extraData = {}) {
  const userRef = doc(db, COLLECTIONS.USERS, user.uid);
  const snapshot = await getDoc(userRef);
  if (!snapshot.exists()) {
    await setDoc(userRef, {
      uid:         user.uid,
      email:       user.email,
      displayName: extraData.displayName ?? user.displayName ?? '',
      createdAt:   serverTimestamp(),
      updatedAt:   serverTimestamp(),
    });
  }
  return userRef;
}

/**
 * Fetch a user document by uid.
 * @param {string} uid
 */
export async function getUserDocument(uid) {
  const snap = await getDoc(doc(db, COLLECTIONS.USERS, uid));
  return snap.exists() ? { id: snap.id, ...snap.data() } : null;
}

// ─── projects ─────────────────────────────────────────────────────────────────

/**
 * Create a new project.
 * @param {string} uid  Owner user id
 * @param {{ name: string, description?: string }} data
 */
export async function createProject(uid, data) {
  return addDoc(collection(db, COLLECTIONS.PROJECTS), {
    ...data,
    ownerId:   uid,
    createdAt: serverTimestamp(),
    updatedAt: serverTimestamp(),
  });
}

/**
 * Get all projects belonging to a user.
 * @param {string} uid
 */
export async function getUserProjects(uid) {
  const q = query(
    collection(db, COLLECTIONS.PROJECTS),
    where('ownerId', '==', uid),
    orderBy('createdAt', 'desc'),
  );
  const snap = await getDocs(q);
  return snap.docs.map((d) => ({ id: d.id, ...d.data() }));
}

// ─── code_reviews ─────────────────────────────────────────────────────────────

/**
 * Create a new code review record.
 * @param {string} uid
 * @param {{ projectId: string, projectName: string, language: string, code: string }} data
 */
export async function createCodeReview(uid, data) {
  return addDoc(collection(db, COLLECTIONS.CODE_REVIEWS), {
    ...data,
    authorId:  uid,
    status:    'pending',   // pending | completed | failed
    createdAt: serverTimestamp(),
    updatedAt: serverTimestamp(),
  });
}

/**
 * Get all code reviews for a user, newest first.
 * @param {string} uid
 */
export async function getUserCodeReviews(uid) {
  const q = query(
    collection(db, COLLECTIONS.CODE_REVIEWS),
    where('authorId', '==', uid),
    orderBy('createdAt', 'desc'),
  );
  const snap = await getDocs(q);
  return snap.docs.map((d) => ({ id: d.id, ...d.data() }));
}

/**
 * Update a code review (e.g. attach AI results).
 * @param {string} reviewId
 * @param {object} data
 */
export async function updateCodeReview(reviewId, data) {
  return updateDoc(doc(db, COLLECTIONS.CODE_REVIEWS, reviewId), {
    ...data,
    updatedAt: serverTimestamp(),
  });
}

// ─── review_issues ────────────────────────────────────────────────────────────

/**
 * Add an issue found during a code review.
 * @param {{ reviewId: string, severity: string, message: string, line?: number }} data
 */
export async function createReviewIssue(data) {
  return addDoc(collection(db, COLLECTIONS.REVIEW_ISSUES), {
    ...data,
    createdAt: serverTimestamp(),
  });
}

/**
 * Get all issues for a specific review.
 * @param {string} reviewId
 */
export async function getIssuesByReview(reviewId) {
  const q = query(
    collection(db, COLLECTIONS.REVIEW_ISSUES),
    where('reviewId', '==', reviewId),
  );
  const snap = await getDocs(q);
  return snap.docs.map((d) => ({ id: d.id, ...d.data() }));
}

// ─── feedback ─────────────────────────────────────────────────────────────────

/**
 * Record user feedback on a suggestion (accepted / rejected).
 * @param {string} uid
 * @param {{ reviewId: string, issueId: string, decision: 'accepted' | 'rejected', note?: string }} data
 */
export async function submitFeedback(uid, data) {
  return addDoc(collection(db, COLLECTIONS.FEEDBACK), {
    ...data,
    userId:    uid,
    createdAt: serverTimestamp(),
  });
}

/**
 * Get all feedback submitted by a user.
 * @param {string} uid
 */
export async function getUserFeedback(uid) {
  const q = query(
    collection(db, COLLECTIONS.FEEDBACK),
    where('userId', '==', uid),
    orderBy('createdAt', 'desc'),
  );
  const snap = await getDocs(q);
  return snap.docs.map((d) => ({ id: d.id, ...d.data() }));
}

// ─── team_standards ───────────────────────────────────────────────────────────

/**
 * Save or update a team standard document.
 * @param {string} standardId  Use a fixed id (e.g. team uid) to overwrite.
 * @param {{ rules: string, language?: string, updatedBy: string }} data
 */
export async function setTeamStandard(standardId, data) {
  return setDoc(
    doc(db, COLLECTIONS.TEAM_STANDARDS, standardId),
    { ...data, updatedAt: serverTimestamp() },
    { merge: true },
  );
}

/**
 * Fetch a team standard document.
 * @param {string} standardId
 */
export async function getTeamStandard(standardId) {
  const snap = await getDoc(doc(db, COLLECTIONS.TEAM_STANDARDS, standardId));
  return snap.exists() ? { id: snap.id, ...snap.data() } : null;
}

/**
 * Delete a team standard document.
 * @param {string} standardId
 */
export async function deleteTeamStandard(standardId) {
  return deleteDoc(doc(db, COLLECTIONS.TEAM_STANDARDS, standardId));
}
