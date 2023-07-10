#!/usr/bin/env python3

# Copyright (C) 2021-2023 Guillaume Jouvet <guillaume.jouvet@unil.ch>
# Published under the GNU GPL (Version 3), check at the LICENSE file 

"""
This IGM module implments the former particle tracking routine.

==============================================================================

Input: uubar,vbar, uvelbase, vvelbase, uvelsurf, vvelsurf
Output: self.xpos, ...
"""

import numpy as np
import os, sys, shutil
import matplotlib.pyplot as plt
import datetime, time
import tensorflow as tf

from igm.modules.utils import *


def params_particles_v1(parser):
    parser.add_argument(
        "--tracking_method",
        type=str,
        default="3d",
        help="Method for tracking particles (3d or simple)",
    )
    parser.add_argument(
        "--frequency_seeding",
        type=int,
        default=10,
        help="Frequency of seeding (default: 10)",
    )
    parser.add_argument(
        "--density_seeding",
        type=int,
        default=0.2,
        help="Density of seeding (default: 0.2)",
    )


def init_particles_v1(params, self):
    self.tlast_seeding = -1.0e5000
    self.tcomp_particles = []

    # initialize trajectories
    self.xpos = tf.Variable([])
    self.ypos = tf.Variable([])
    self.zpos = tf.Variable([])
    self.rhpos = tf.Variable([])
    self.wpos = tf.Variable([])  # this is to give a weight to the particle
    self.tpos = tf.Variable([])
    self.englt = tf.Variable([])

    # build the gridseed
    self.gridseed = np.zeros_like(self.thk) == 1
    rr = int(1.0 / params.density_seeding)
    self.gridseed[::rr, ::rr] = True


def update_particles_v1(params, self):

    import tensorflow_addons as tfa

    self.logger.info("Update particle tracking at time : " + str(self.t.numpy()))

    if (self.t.numpy() - self.tlast_seeding) >= params.frequency_seeding:
        seeding_particles(params, self)

        # merge the new seeding points with the former ones
        self.xpos = tf.Variable(tf.concat([self.xpos, self.nxpos], axis=-1))
        self.ypos = tf.Variable(tf.concat([self.ypos, self.nypos], axis=-1))
        self.zpos = tf.Variable(tf.concat([self.zpos, self.nzpos], axis=-1))
        self.rhpos = tf.Variable(tf.concat([self.rhpos, self.nrhpos], axis=-1))
        self.wpos = tf.Variable(tf.concat([self.wpos, self.nwpos], axis=-1))
        self.tpos = tf.Variable(tf.concat([self.tpos, self.ntpos], axis=-1))
        self.englt = tf.Variable(tf.concat([self.englt, self.nenglt], axis=-1))

        self.tlast_seeding = self.t.numpy()

    self.tcomp_particles.append(time.time())

    # find the indices of trajectories
    # these indicies are real values to permit 2D interpolations
    i = (self.xpos - self.x[0]) / self.dx
    j = (self.ypos - self.y[0]) / self.dx

    indices = tf.expand_dims(
        tf.concat([tf.expand_dims(j, axis=-1), tf.expand_dims(i, axis=-1)], axis=-1),
        axis=0,
    )

    uvelbase = tfa.image.interpolate_bilinear(
        tf.expand_dims(tf.expand_dims(self.uvelbase, axis=0), axis=-1),
        indices,
        indexing="ij",
    )[0, :, 0]

    vvelbase = tfa.image.interpolate_bilinear(
        tf.expand_dims(tf.expand_dims(self.vvelbase, axis=0), axis=-1),
        indices,
        indexing="ij",
    )[0, :, 0]

    uvelsurf = tfa.image.interpolate_bilinear(
        tf.expand_dims(tf.expand_dims(self.uvelsurf, axis=0), axis=-1),
        indices,
        indexing="ij",
    )[0, :, 0]

    vvelsurf = tfa.image.interpolate_bilinear(
        tf.expand_dims(tf.expand_dims(self.vvelsurf, axis=0), axis=-1),
        indices,
        indexing="ij",
    )[0, :, 0]

    othk = tfa.image.interpolate_bilinear(
        tf.expand_dims(tf.expand_dims(self.thk, axis=0), axis=-1),
        indices,
        indexing="ij",
    )[0, :, 0]

    topg = tfa.image.interpolate_bilinear(
        tf.expand_dims(tf.expand_dims(self.topg, axis=0), axis=-1),
        indices,
        indexing="ij",
    )[0, :, 0]

    smb = tfa.image.interpolate_bilinear(
        tf.expand_dims(tf.expand_dims(self.smb, axis=0), axis=-1),
        indices,
        indexing="ij",
    )[0, :, 0]

    if params.tracking_method == "simple":
        nthk = othk + smb * self.dt  # new ice thicnkess after smb update

        # adjust the relative height within the ice column with smb
        self.rhpos = tf.where(
            nthk > 0.1, tf.clip_by_value(self.rhpos * othk / nthk, 0, 1), 1
        )

        uvel = uvelbase + (uvelsurf - uvelbase) * (
            1 - (1 - self.rhpos) ** 4
        )  # SIA-like
        vvel = vvelbase + (vvelsurf - vvelbase) * (
            1 - (1 - self.rhpos) ** 4
        )  # SIA-like

        self.xpos = self.xpos + self.dt * uvel  # forward euler
        self.ypos = self.ypos + self.dt * vvel  # forward euler

        self.zpos = topg + nthk * self.rhpos

    elif params.tracking_method == "3d":
        # This was a test of smoothing the surface topography to regaluraze the vertical velocitiy.
        #                import tensorflow_addons as tfa
        #                susurf = tfa.image.gaussian_filter2d(self.usurf, sigma=5, filter_shape=5, padding="CONSTANT")
        #                stopg  = tfa.image.gaussian_filter2d(self.topg , sigma=3, filter_shape=5, padding="CONSTANT")

        slopsurfx, slopsurfy = compute_gradient_tf(self.usurf, self.dx, self.dx)
        sloptopgx, sloptopgy = compute_gradient_tf(self.topg, self.dx, self.dx)

        self.divflux = compute_divflux(self.ubar, self.vbar, self.thk, self.dx, self.dx)

        # the vertical velocity is the scalar product of horizont. velo and bedrock gradient
        self.wvelbase = self.uvelbase * sloptopgx + self.vvelbase * sloptopgy
        # Using rules of derivative the surface vertical velocity can be found from the
        # divergence of the flux considering that the ice 3d velocity is divergence-free.
        self.wvelsurf = (
            self.uvelsurf * slopsurfx + self.vvelsurf * slopsurfy - self.divflux
        )

        wvelbase = tfa.image.interpolate_bilinear(
            tf.expand_dims(tf.expand_dims(self.wvelbase, axis=0), axis=-1),
            indices,
            indexing="ij",
        )[0, :, 0]

        wvelsurf = tfa.image.interpolate_bilinear(
            tf.expand_dims(tf.expand_dims(self.wvelsurf, axis=0), axis=-1),
            indices,
            indexing="ij",
        )[0, :, 0]

        #           print('at the surface? : ',all(self.zpos == topg+othk))

        # make sure the particle remian withi the ice body
        self.zpos = tf.clip_by_value(self.zpos, topg, topg + othk)

        # get the relative height
        self.rhpos = tf.where(othk > 0.1, (self.zpos - topg) / othk, 1)

        uvel = uvelbase + (uvelsurf - uvelbase) * (
            1 - (1 - self.rhpos) ** 4
        )  # SIA-like
        vvel = vvelbase + (vvelsurf - vvelbase) * (
            1 - (1 - self.rhpos) ** 4
        )  # SIA-like
        wvel = wvelbase + (wvelsurf - wvelbase) * (
            1 - (1 - self.rhpos) ** 4
        )  # SIA-like

        self.xpos = self.xpos + self.dt * uvel  # forward euler
        self.ypos = self.ypos + self.dt * vvel  # forward euler
        self.zpos = self.zpos + self.dt * wvel  # forward euler

        # make sur the particle remains in the horiz. comp. domain
        self.xpos = tf.clip_by_value(self.xpos, self.x[0], self.x[-1])
        self.ypos = tf.clip_by_value(self.ypos, self.y[0], self.y[-1])

    indices = tf.concat(
        [
            tf.expand_dims(tf.cast(j, dtype="int32"), axis=-1),
            tf.expand_dims(tf.cast(i, dtype="int32"), axis=-1),
        ],
        axis=-1,
    )
    updates = tf.cast(tf.where(self.rhpos == 1, self.wpos, 0), dtype="float32")

    # this computes the sum of the weight of particles on a 2D grid
    self.weight_particles = tf.tensor_scatter_nd_add(
        tf.zeros_like(self.thk), indices, updates
    )

    # compute the englacial time
    self.englt = self.englt + tf.cast(
        tf.where(self.rhpos < 1, self.dt, 0.0), dtype="float32"
    )

    self.tcomp_particles[-1] -= time.time()
    self.tcomp_particles[-1] *= -1


def final_particles_v1(params, self):
    pass
    

def seeding_particles(params, self):
    """
    here we define (xpos,ypos) the horiz coordinate of tracked particles
    and rhpos is the relative position in the ice column (scaled bwt 0 and 1)

    here we seed only the accum. area (a bit more), where there is
    significant ice, and in some points of a regular grid self.gridseed
    (density defined by density_seeding)

    """

    #        This will serve to remove imobile particles, but it is not active yet.

    #        indices = tf.expand_dims( tf.concat(
    #                       [tf.expand_dims((self.ypos - self.y[0]) / self.dx, axis=-1),
    #                        tf.expand_dims((self.xpos - self.x[0]) / self.dx, axis=-1)],
    #                       axis=-1 ), axis=0)

    #        import tensorflow_addons as tfa

    #        thk = tfa.image.interpolate_bilinear(
    #                    tf.expand_dims(tf.expand_dims(self.thk, axis=0), axis=-1),
    #                    indices,indexing="ij",      )[0, :, 0]

    #        J = (thk>1)

    I = (
        (self.thk > 10) & (self.smb > -2) & self.gridseed
    )  # seed where thk>10, smb>-2, on a coarse grid
    self.nxpos = self.X[I]
    self.nypos = self.Y[I]
    self.nzpos = self.usurf[I]
    self.nrhpos = tf.ones_like(self.X[I])
    self.nwpos = tf.ones_like(self.X[I])
    self.ntpos = tf.ones_like(self.X[I]) * self.t
    self.nenglt = tf.zeros_like(self.X[I])
